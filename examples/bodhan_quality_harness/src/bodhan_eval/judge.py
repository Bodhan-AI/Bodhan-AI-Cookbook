"""LLM judge.

Used for what deterministic checks cannot see: meaning-level mistranslations,
and localizing omissions precisely. Three rules keep it honest:

* It **reads** Indian-language text but never writes it — the harness evaluates,
  it does not fix.
* It is given the deterministic flags already found, so it spends its attention
  on what is left rather than re-deriving the easy cases.
* It runs at temperature 0 and is cached, and we measure its self-consistency
  rather than assuming it.
"""

from __future__ import annotations

import json
import re
import sys
import types
from dataclasses import dataclass

# The `openai` SDK imports `jiter` (a native Rust extension) the first time its
# chat-completions resource is touched. Some locked-down Windows environments
# block unsigned DLLs via Application Control policy, so that import fails even
# though our non-streaming path never actually calls into jiter. Install a
# stdlib-backed shim only when the real extension is unavailable, so the SDK can
# load and the (unused) streaming code path degrades to the standard library.
try:  # pragma: no cover - depends on the host environment
    import jiter  # noqa: F401
except Exception:  # noqa: BLE001 - blocked/missing native extension
    _jiter_shim = types.ModuleType("jiter")

    def _from_json(data, *args, **kwargs):  # mimic jiter.from_json signature
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")
        return json.loads(data)

    _jiter_shim.from_json = _from_json
    sys.modules["jiter"] = _jiter_shim

from openai import AsyncOpenAI

from . import config
from .cache import Cache
from .checks.base import Flag, ItemContext

RUBRIC = """You are a translation quality reviewer for Indian-language content.

You will be given an English source, a machine translation, and any issues already
found by deterministic checks. Your job is to find ONLY the issues those checks
cannot see, and to localize them.

Categories you may use:
- mistranslation: wrong meaning of a word, phrase or clause
- omission: a clause or sentence of the source is missing from the translation
- addition: content in the translation that is not in the source
- term: a name or technical term rendered wrongly or inconsistently

Severity: minor (does not impede understanding), major (misleads the reader),
critical (changes a fact, number, or safety-relevant instruction).

Rules:
- Report the character span [start, end] in the TRANSLATION text that is wrong.
  For omissions, span the point in the translation where the missing content
  should appear; if there is no such point, use null.
- Do not report anything already listed in "already found".
- Do not rewrite or correct the translation. You are reporting, not fixing.
- If you are unsure, omit it. A false alarm is worse than a missed subtlety.
- Reply with JSON only.

Output schema:
{"flags": [{"category": "...", "severity": "...", "span": [start, end] or null,
            "explanation": "one sentence naming what is wrong and where"}]}
"""


@dataclass
class JudgeResult:
    flags: list[Flag]
    raw: str
    ok: bool
    error: str | None = None


class Judge:
    def __init__(self, cache: Cache, *, offline: bool = False,
                 temperature: float = 0.0, timeout: float = 60.0) -> None:
        self.cache = cache
        self.offline = offline
        self.temperature = temperature
        self.timeout = timeout
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=config.LLM_BASE_URL.rstrip("/"),
                api_key=config.LLM_API_KEY,
                default_headers={"api-subscription-key": config.LLM_API_KEY},
                timeout=self.timeout,
                max_retries=0,
            )
        return self._client

    async def review(self, ctx: ItemContext, already: list[Flag],
                     *, use_cache: bool = True) -> JudgeResult:
        if self.offline or not config.have_judge():
            return JudgeResult([], "", False, "judge unavailable (no LLM_* env or offline)")

        payload = {
            "source": ctx.source,
            "translation": ctx.translation,
            "language": ctx.lang,
            "already found": [
                {"category": f.category, "span": list(f.span) if f.span else None,
                 "evidence": f.evidence[:160]}
                for f in already
            ],
        }
        key = Cache.key("judge", config.LLM_MODEL, self.temperature,
                        json.dumps(payload, sort_keys=True, ensure_ascii=False))
        if use_cache:
            cached = self.cache.get_json(key)
            if cached is not None:
                return JudgeResult(self._to_flags(cached.get("flags", [])),
                                   cached.get("raw", ""), True)

        try:
            client = self._get_client()
            completion = await client.chat.completions.create(
                model=config.LLM_MODEL,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": RUBRIC},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
            )
            raw = completion.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 - judge failure must not kill the run
            return JudgeResult([], "", False, f"judge call failed: {type(exc).__name__}")

        data = _extract_json(raw)
        flags = data.get("flags", []) if isinstance(data, dict) else []
        if use_cache:
            # Only the canonical (cached) sample is stored; a consistency re-ask
            # must never overwrite it.
            self.cache.put_json(key, {"flags": flags, "raw": raw})
        return JudgeResult(self._to_flags(flags), raw, True)

    @staticmethod
    def _to_flags(items: list[dict]) -> list[Flag]:
        out: list[Flag] = []
        for it in items:
            cat = str(it.get("category", "")).strip().lower()
            if cat not in ("mistranslation", "omission", "addition", "term"):
                continue
            sev = str(it.get("severity", "major")).strip().lower()
            if sev not in ("minor", "major", "critical"):
                sev = "major"
            span = it.get("span")
            span_t = None
            if isinstance(span, (list, tuple)) and len(span) == 2:
                try:
                    span_t = (int(span[0]), int(span[1]))
                except (TypeError, ValueError):
                    span_t = None
            out.append(Flag(
                category=cat, severity=sev, span=span_t,
                evidence=str(it.get("explanation", ""))[:300],
                check="judge.llm", confidence=0.6,
            ))
        return out


def _extract_json(raw: str) -> dict:
    """Tolerate code fences and surrounding prose."""
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*(.+?)```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
