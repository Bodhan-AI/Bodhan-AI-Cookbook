"""LLM-as-a-judge for translation errors, using a local Ollama model.

The judge only *labels* errors. It never rewrites, corrects or generates
translations: the schema has no field for a corrected text, extra fields are
rejected, and every finding must quote evidence that already exists in the
source (omissions) or in the translation (everything else).

Validation is per finding. One malformed or unsupported finding is dropped
(and recorded in ``rejected``) without discarding the valid ones next to it.
Only a response that is not a JSON object with a ``findings`` list is treated
as malformed as a whole.

Raw model output is cached on disk keyed by model, prompt hash, source,
translation, target language and decoding options, so re-running the same
evaluation never triggers a second inference. Validation runs again on every
read, so improving the validators does not require re-inference.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bodhan_eval.cache import JsonCache, stable_hash
from bodhan_eval.lexicons import LANGUAGE_NAMES

DEFAULT_MODEL = "llama3.2:3b"
DEFAULT_BASE_URL = "http://localhost:11434"
PROMPT_VERSION = "2026-11-23.v2"

JUDGE_PROMPT = """
You are a strict multilingual translation quality evaluator.

Your ONLY task is to identify errors in a translation from English into an
Indic language. NEVER rewrite, correct, or generate a replacement
translation. Do not output any corrected text.

Compare the English SOURCE with the TRANSLATION and report errors in these
categories only:

- omission: source content that is missing from the translation
- addition: content in the translation that is not in the source
- mistranslation: content whose meaning was changed
- number_unit: a wrong number, date, time, currency or unit
- term_entity: a wrong name, brand, place, product or technical term
- format: broken Markdown, LaTeX, code, links or other formatting
- wrong_language_script: text in the wrong language or script

Severity: low, medium or high.

EVIDENCE RULES (a finding that breaks them is discarded):

1. For "omission", set evidence_source to "source" and copy the span
   verbatim from the English SOURCE (the words that are missing).
2. For every other category, set evidence_source to "translation" and copy
   the span verbatim from the TRANSLATION.
3. The span must be an exact substring of that text. Never translate,
   paraphrase or invent a span.
4. If you cannot quote an exact span, do not report the finding.
5. Do not report missing English function words ("at", "the", "is") unless
   their absence changes the meaning.

Example 1

SOURCE:
The shop opens at 9 AM on Sunday.

TRANSLATION:
दुकान रविवार को शाम 7 बजे खुलती है।

Output:
{"findings": [{"evidence_source": "translation", "category": "number_unit",
  "severity": "high", "span": "शाम 7 बजे",
  "explanation": "The source says 9 AM; the translation says 7 PM.",
  "confidence": 0.95}]}

Example 2

SOURCE:
Send the signed form to the bank by Monday.

TRANSLATION:
हस्ताक्षरित फॉर्म बैंक को भेजें।

Output:
{"findings": [{"evidence_source": "source", "category": "omission",
  "severity": "medium", "span": "by Monday",
  "explanation": "The deadline is missing from the translation.",
  "confidence": 0.9}]}

If there are no errors, return exactly: {"findings": []}

Return ONLY JSON with the "findings" list. No other fields.
"""

USER_TEMPLATE = """Target language: {language}

SOURCE (English):
{source}

TRANSLATION:
{translation}

Evaluate the translation. Return JSON only."""


class JudgeError(RuntimeError):
    """Base class for judge failures."""


class JudgeUnavailableError(JudgeError):
    """Ollama is not reachable, timed out, or the model is not installed."""


class JudgeResponseError(JudgeError, ValueError):
    """The model returned something that is not a findings object."""


class EvidenceSource(str, Enum):
    SOURCE = "source"
    TRANSLATION = "translation"


class ErrorCategory(str, Enum):
    OMISSION = "omission"
    ADDITION = "addition"
    MISTRANSLATION = "mistranslation"
    NUMBER_UNIT = "number_unit"
    TERM_ENTITY = "term_entity"
    FORMAT = "format"
    WRONG_LANGUAGE_SCRIPT = "wrong_language_script"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_source: EvidenceSource
    category: ErrorCategory
    severity: Severity
    span: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[Finding] = Field(default_factory=list)


def _schema_json() -> str:
    return json.dumps(JudgeResult.model_json_schema(), sort_keys=True)


PROMPT_HASH = hashlib.sha256(
    (PROMPT_VERSION + JUDGE_PROMPT + USER_TEMPLATE + _schema_json()).encode("utf-8")
).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Evidence validation
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def expected_evidence_source(category: ErrorCategory) -> EvidenceSource:
    return (
        EvidenceSource.SOURCE
        if category == ErrorCategory.OMISSION
        else EvidenceSource.TRANSLATION
    )


def evidence_problem(finding: Finding, source_text: str, translated_text: str) -> str | None:
    """Return why a finding's evidence is invalid, or ``None`` if it is valid.

    Spans are compared after NFC normalisation and whitespace collapsing, so
    a span differing only in Unicode composition or spacing is accepted.
    """
    expected = expected_evidence_source(finding.category)
    if finding.evidence_source != expected:
        return f"wrong_evidence_source: {finding.category.value} evidence must come from the {expected.value}"

    haystack = source_text if expected == EvidenceSource.SOURCE else translated_text
    if _norm(finding.span) not in _norm(haystack):
        return f"span_not_found_in_{expected.value}"

    return None


def validate_evidence_spans(
    result: JudgeResult,
    source_text: str,
    translated_text: str,
) -> JudgeResult:
    """Keep only findings whose evidence is valid (backwards-compatible API)."""
    return JudgeResult(findings=[
        finding for finding in result.findings
        if evidence_problem(finding, source_text, translated_text) is None
    ])


@dataclass
class ParsedJudgement:
    findings: list[Finding] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


def parse_judge_response(
    content: str,
    source_text: str,
    translated_text: str,
) -> ParsedJudgement:
    """Parse raw model output, validating each finding independently."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise JudgeResponseError(f"Judge output is not JSON: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise JudgeResponseError("Judge output must be an object with a 'findings' list.")

    parsed = ParsedJudgement()

    # Anything besides "findings" (for example a "corrected_translation")
    # is discarded and never surfaced: the judge must not rewrite.
    for key in sorted(set(data) - {"findings"}):
        parsed.rejected.append({"reason": "unexpected_top_level_field", "field": key})

    seen = set()
    for raw in data["findings"]:
        try:
            finding = Finding.model_validate(raw)
        except ValidationError as exc:
            parsed.rejected.append({
                "reason": "schema",
                "errors": [
                    {"loc": list(err["loc"]), "msg": err["msg"]}
                    for err in exc.errors()
                ],
                "finding": raw,
            })
            continue

        problem = evidence_problem(finding, source_text, translated_text)
        if problem:
            parsed.rejected.append({"reason": problem, "finding": finding.model_dump(mode="json")})
            continue

        identity = (finding.category, _norm(finding.span))
        if identity in seen:
            continue
        seen.add(identity)
        parsed.findings.append(finding)

    return parsed


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------

@dataclass
class JudgeOutcome:
    status: str  # "ok" | "error" | "unavailable"
    findings: list[Finding] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    cached: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "cached": self.cached,
            "error": self.error,
            "findings": [f.model_dump(mode="json") for f in self.findings],
            "rejected": self.rejected,
        }


def language_name(language: str) -> str:
    return LANGUAGE_NAMES.get(language, language)


class OllamaJudge:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 120,
        cache_path: str | Path | None = None,
        session: requests.Session | None = None,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.cache = JsonCache(cache_path)
        self.inference_calls = 0
        self.options = {"temperature": 0, "seed": 0}

    # -- availability ------------------------------------------------------

    def check_available(self) -> None:
        """Raise :class:`JudgeUnavailableError` unless the model is served."""
        try:
            response = self.session.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            names = {m.get("name") for m in response.json().get("models", [])}
        except (requests.RequestException, ValueError) as exc:
            raise JudgeUnavailableError(f"Ollama is not reachable at {self.base_url}: {exc}") from exc

        if self.model not in names and f"{self.model}:latest" not in names:
            raise JudgeUnavailableError(
                f"Model {self.model!r} is not installed in Ollama. Run: ollama pull {self.model}"
            )

    # -- caching -----------------------------------------------------------

    def cache_key(self, source_text: str, translated_text: str, language: str) -> str:
        return stable_hash({
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "prompt_hash": PROMPT_HASH,
            "source": source_text,
            "translation": translated_text,
            "language": language,
            "options": self.options,
        })

    # -- inference ---------------------------------------------------------

    def _infer(self, source_text: str, translated_text: str, language: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "format": JudgeResult.model_json_schema(),
            "messages": [
                {"role": "system", "content": JUDGE_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(
                    language=language_name(language),
                    source=source_text,
                    translation=translated_text,
                )},
            ],
            "options": self.options,
        }

        self.inference_calls += 1
        try:
            response = self.session.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise JudgeUnavailableError(f"Ollama request failed: {exc}") from exc
        except requests.RequestException as exc:
            raise JudgeUnavailableError(f"Ollama returned an error: {exc}") from exc
        except ValueError as exc:
            raise JudgeResponseError(f"Ollama response was not JSON: {exc}") from exc

        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise JudgeResponseError(f"Ollama response had no message content: {exc}") from exc

        if not isinstance(content, str):
            raise JudgeResponseError("Ollama message content is not a string.")
        return content

    def judge(self, source_text: str, translated_text: str, language: str) -> JudgeOutcome:
        """Judge one translation. Never raises for model/transport problems."""
        if not source_text.strip() or not translated_text.strip():
            return JudgeOutcome(status="error", error="Source and translation cannot be empty.")

        key = self.cache_key(source_text, translated_text, language)
        cached = self.cache.get(key)
        from_cache = cached is not None

        try:
            if from_cache:
                content = cached["raw_content"]
            else:
                content = self._infer(source_text, translated_text, language)
            parsed = parse_judge_response(content, source_text, translated_text)
        except JudgeUnavailableError as exc:
            return JudgeOutcome(status="unavailable", error=str(exc))
        except JudgeResponseError as exc:
            # Malformed output is not cached, so it is retried next run.
            return JudgeOutcome(status="error", error=str(exc), cached=from_cache)

        if not from_cache:
            self.cache.set(key, {
                "raw_content": content,
                "model": self.model,
                "prompt_version": PROMPT_VERSION,
                "prompt_hash": PROMPT_HASH,
                "language": language,
            })

        return JudgeOutcome(
            status="ok",
            findings=parsed.findings,
            rejected=parsed.rejected,
            cached=from_cache,
        )

    def evaluate(
        self,
        source_text: str,
        translated_text: str,
        language: str,
    ) -> JudgeResult:
        """Original API: return validated findings or raise.

        Raises ``ValueError`` for empty input or malformed output and
        ``RuntimeError`` (:class:`JudgeUnavailableError`) when Ollama fails.
        """
        if not source_text.strip() or not translated_text.strip():
            raise ValueError("Source and translation cannot be empty.")

        outcome = self.judge(source_text, translated_text, language)
        if outcome.status == "unavailable":
            raise JudgeUnavailableError(outcome.error)
        if outcome.status == "error":
            raise JudgeResponseError(outcome.error)
        return JudgeResult(findings=outcome.findings)


def findings_to_checks(outcome: JudgeOutcome) -> list[dict]:
    """Convert judge findings into ``review`` checks for the report.

    LLM findings are never counted as confirmed failures: a 3B local model
    produces semantic false positives, so a human (or a calibrated
    threshold) must confirm them.
    """
    return [
        {
            "category": f"llm_{finding.category.value}",
            "status": "review",
            "passed": None,
            "severity": finding.severity.value,
            "source": "llm_judge",
            "evidence": {
                "evidence_source": finding.evidence_source.value,
                "span": finding.span,
                "explanation": finding.explanation,
                "confidence": finding.confidence,
            },
        }
        for finding in outcome.findings
    ]
