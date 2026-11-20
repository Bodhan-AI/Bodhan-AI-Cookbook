"""The LLM 'brain': any OpenAI-compatible endpoint, read from LLM_BASE_URL, LLM_MODEL, LLM_API_KEY.

The LLM only ever reads and writes English. Indian-language text comes from Bodhan models.

The LLM is optional: every caller has a deterministic fallback. So when it is missing or down, the
run must not wait on it. A circuit breaker opens on the first call that fails for good (retries
exhausted, or a bad key, model or URL), and from then on every call fails at once instead of
retrying. Cached replies are still served, so a rerun keeps the plan it already paid for.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import openai
from openai import OpenAI

log = logging.getLogger(__name__)

TRIES = 3
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "90"))  # seconds per request
RETRY_STATUS = {408, 409, 429}  # plus every 5xx
BROKEN_STATUS = {401, 403, 404}  # bad key, no access, unknown model or URL: no call will ever work


class LLMError(RuntimeError):
    pass


class LLMTruncated(LLMError):
    """The reply hit the model's output limit. Callers retry with less work per call."""


class LLM:
    def __init__(self, cache_dir: Path):
        self.model = os.environ.get("LLM_MODEL", "").strip()
        key = os.environ.get("LLM_API_KEY", "").strip()
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.failures = 0
        self.down = ""  # why the LLM is off for the rest of the run; empty while it is usable
        self._lock = threading.Lock()
        self.client = None
        if not (self.model and key):
            self._open("LLM_MODEL or LLM_API_KEY is not set")
            return
        self.client = OpenAI(base_url=os.environ.get("LLM_BASE_URL") or None, api_key=key,
                             max_retries=0, timeout=TIMEOUT)

    def ask_json(self, prompt: str) -> dict:
        """Send one prompt, return the JSON object in the reply. Cached by prompt."""
        key = hashlib.sha256(f"{self.model}\0{prompt}".encode()).hexdigest()[:32]
        path = self.cache / f"llm-{key}.json"
        if path.exists():
            return json.loads(path.read_text())

        try:
            value = _parse_json(self._chat(prompt))
        except LLMError:
            with self._lock:
                self.failures += 1
            raise
        path.write_text(json.dumps(value, ensure_ascii=False))
        return value

    def summary(self) -> dict:
        """For report.json: did the run have its LLM, and if not, why."""
        return {"model": self.model or None, "available": not self.down, "down_reason": self.down or None,
                "calls": self.calls, "failures": self.failures}

    def _chat(self, prompt: str) -> str:
        problem = ""
        for attempt in range(TRIES):
            if self.down:
                raise LLMError(f"LLM unavailable: {self.down}")
            try:
                r = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                )
            except openai.APIStatusError as e:
                problem = f"HTTP {e.status_code}: {str(e)[:200]}"
                if e.status_code in BROKEN_STATUS:
                    self._open(problem)
                    raise LLMError(problem) from e
                if e.status_code not in RETRY_STATUS and e.status_code < 500:
                    raise LLMError(problem) from e  # this prompt was rejected; the next one may be fine
            except Exception as e:  # timeouts and connection errors: worth a retry
                problem = f"{type(e).__name__}: {str(e)[:200]}"
            else:
                with self._lock:
                    self.calls += 1
                if r.choices:
                    if r.choices[0].finish_reason == "length":
                        raise LLMTruncated("reply cut off at the model's output limit")
                    return r.choices[0].message.content or ""
                problem = "reply had no choices"
            if attempt + 1 < TRIES:
                wait = 2 ** (attempt + 1)
                log.warning("LLM call failed (%s), retry in %ss", problem, wait)
                time.sleep(wait)
        self._open(f"still failing after {TRIES} tries: {problem}")
        raise LLMError(f"LLM unavailable: {self.down}")

    def _open(self, reason: str) -> None:
        with self._lock:
            if self.down:
                return
            self.down = reason
        log.warning("LLM off for the rest of the run (%s). Continuing without it: raw transcript sentences, "
                    "no glossary from the LLM, and overlong lines are sped up instead of shortened.", reason)


REASONING = re.compile(r"<(reasoning|think|thinking)>.*?(</\1>|$)", re.DOTALL | re.IGNORECASE)


def _parse_json(text: str) -> dict:
    """Pull the JSON object out of a reply. Reasoning models may put their thinking in the reply as
    <reasoning>...</reasoning> or <think>...</think>, often with braces of their own: that is dropped,
    and the last top-level JSON object in what remains is the answer (fenced or not)."""
    text = REASONING.sub("", text)
    decoder, found, i = json.JSONDecoder(), None, text.find("{")
    while i != -1:
        try:
            value, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(value, dict):
            found = value
        i = text.find("{", end)
    if found is None:
        raise LLMError(f"no JSON object in LLM reply: {text[:200]!r}")
    return found
