"""The workflow's "brain": an OpenAI-compatible LLM used only for judgement on the English side.

Allowed here: grouping fragments into sentences, building a glossary, shortening English, judging a
dubbed line. Never allowed: writing Indian-language text, transcribing or synthesizing speech. Output that
contains Indic script is rejected. Every call has a deterministic fallback so the pipeline runs without an LLM.
"""
import json
import os
import re
import threading
import time

from . import segmentation
from .logs import log

INDIC = re.compile(r"[ऀ-෿؀-ۿ᱐-᱿]")


def contains_indic(s: str) -> bool:
    return bool(INDIC.search(s or ""))


def _json(text: str):
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    return json.loads(m.group(0)) if m else None


class Brain:
    def __init__(self, client=None, model: str | None = None):
        self.model = model or os.environ.get("LLM_MODEL", "")
        self.client = client
        if client is None and os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY") and self.model:
            from openai import OpenAI
            self.client = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"], max_retries=0)   # retries are ours, below
        self.calls = 0
        self.last_error = ""
        self.max_attempts = max(1, int(os.environ.get("LLM_MAX_ATTEMPTS", "6")))
        self.backoff = float(os.environ.get("LLM_BACKOFF", "2.0"))
        self.gate = threading.Semaphore(max(1, int(os.environ.get("LLM_CONCURRENCY", "2"))))   # languages run in parallel; the LLM is shared
        self.use_temperature = True
        self.breaker = max(1, int(os.environ.get("LLM_BREAKER", "3")))    # consecutive calls that exhaust their retries before the LLM is switched off for the run
        self.exhausted = 0
        self.failed_calls = 0
        self.tripped = False

    @property
    def available(self) -> bool:
        return self.client is not None and not self.tripped

    @staticmethod
    def _why(e: Exception) -> str:
        """What the provider actually said. 'Error code: 429' alone does not tell a rate limit from an exhausted quota."""
        parts = [str(e)]
        for extra in (getattr(e, "body", None), getattr(getattr(e, "response", None), "text", None)):
            if extra:
                parts.append(extra if isinstance(extra, str) else json.dumps(extra, ensure_ascii=False, default=str))
        return " | ".join(dict.fromkeys(p for p in parts if p))[:300]

    def _give_up(self, e: Exception):
        self.failed_calls += 1
        self.exhausted += 1
        self.last_error = self._why(e)
        if self.exhausted >= self.breaker and not self.tripped:
            self.tripped = True      # a provider that fails this often will not recover mid-run: stop waiting on it, use the fallbacks
            log("llm_disabled", reason=f"{self.exhausted} calls in a row failed after retries; fallbacks are used for the rest of the run",
                last_error=self.last_error)
        raise e

    def _ask(self, system: str, user: str):
        """One chat call with a concurrency cap and retries. A 429 or 5xx waits (Retry-After if the provider sends it, else
        exponential backoff) and tries again, so a busy provider slows the run down instead of silently disabling the
        LLM decision and dropping to its fallback."""
        self.calls += 1
        if self.tripped:
            raise RuntimeError(f"LLM switched off for this run: {self.last_error}")
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last = None
        for k in range(1, self.max_attempts + 1):
            try:
                with self.gate:
                    kw = {"temperature": 0} if self.use_temperature else {}
                    r = self.client.chat.completions.create(model=self.model, messages=msgs, **kw)
                self.exhausted = 0
                return r.choices[0].message.content or ""
            except Exception as e:
                status = getattr(e, "status_code", None)
                if status in (400, 422) and self.use_temperature:
                    self.use_temperature = False                 # some providers reject sampling parameters: retry once without
                    last = e
                    continue
                transient = status in (429, 500, 502, 503, 504) or type(e).__name__ in (
                    "APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError", "TimeoutError", "ConnectError")
                if not transient or k == self.max_attempts:
                    self._give_up(e)
                last = e
                try:
                    ra = float(getattr(getattr(e, "response", None), "headers", {}).get("retry-after", 0) or 0)
                except (TypeError, ValueError, AttributeError):
                    ra = 0.0
                wait = min(60.0, ra or self.backoff * 2 ** (k - 1))
                log("llm_retry", status=status, attempt=k, wait=round(wait, 1), error=self._why(e)[:200])
                time.sleep(wait)
        raise last or RuntimeError("LLM call failed")

    # ---- decision 1: sentence grouping ----
    def group_sentences(self, texts: list[str], chunks: list[dict]) -> tuple[list[list[int]], str]:
        fallback = segmentation.heuristic_groups(texts, chunks)
        if not self.available or len(texts) < 2:
            return fallback, "heuristic"
        try:
            lines = "\n".join(f"{i}: [{chunks[i]['start']:.1f}-{chunks[i]['end']:.1f}s] {t}" for i, t in enumerate(texts))
            out = _json(self._ask(
                "You regroup consecutive speech fragments from an English transcript into complete sentences so each "
                "group can be translated on its own. Keep order. Never split a fragment. Do not exceed 20 seconds per "
                'group. Reply only with JSON: {"groups": [[0,1],[2],...]}.', lines))
            groups = out["groups"] if isinstance(out, dict) else out
            if segmentation.valid_groups(groups, len(texts), chunks):
                return groups, "llm"
            self.last_error = "the LLM returned an invalid grouping"
            log("llm_rejected", decision="group_sentences", reason="invalid partition")
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {str(e)[:160]}"
            log("llm_failed", decision="group_sentences", error=self._why(e)[:200])
        return fallback, "heuristic"

    # ---- decision 2: glossary ----
    def build_glossary(self, full_text: str) -> list[dict]:
        if not self.available:
            return []
        try:
            out = _json(self._ask(
                "List terms in this English lesson transcript that must be translated the same way every time: "
                "person and place names, acronyms, technical or mathematical terms, units. Copy each term exactly "
                "as written. pin=true for names and technical terms that should keep one fixed rendering. At most 40. "
                'Reply only with JSON: {"terms": [{"term": "...", "kind": "name|acronym|term|unit", "pin": true}]}.',
                full_text[:12000]))
            terms = out["terms"] if isinstance(out, dict) else out
            low = full_text.lower()
            return [t for t in terms if isinstance(t, dict) and t.get("term") and t["term"].lower() in low][:40]
        except Exception as e:
            log("llm_failed", decision="build_glossary", error=self._why(e)[:200])
            return []

    # ---- decision 3: shorten English when the dub is too long ----
    def condense(self, english: str, ratio: float, keep_terms: list[str]) -> str | None:
        if not self.available:
            return None
        try:
            t = self._ask(
                f"Rewrite the English sentence to about {int(ratio * 100)}% of its length. Keep the meaning, all numbers "
                f"and these terms unchanged: {keep_terms}. Plain spoken English only. Reply with the sentence only.",
                english).strip().strip('"')
            if t and len(t) < len(english) and not contains_indic(t):
                return t
        except Exception as e:
            log("llm_failed", decision="condense", error=self._why(e)[:200])
        return None

    # ---- decision 4: judge a borderline dubbed line (reads target text, never writes it) ----
    def judge(self, english: str, translation: str, back_transcript: str) -> bool | None:
        if not self.available:
            return None
        try:
            out = _json(self._ask(
                "You check a dubbed line. Compare the English source, the intended translation and what the speech "
                "synthesizer actually said (a transcript of it). Say ok=false only if content is missing, repeated or "
                'changed in meaning. Reply only with JSON: {"ok": true|false}.',
                json.dumps({"english": english, "translation": translation, "spoken": back_transcript},
                           ensure_ascii=False)))
            return bool(out["ok"])
        except Exception as e:
            log("llm_failed", decision="judge", error=self._why(e)[:200])
            return None
