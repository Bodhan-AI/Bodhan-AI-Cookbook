"""Deterministic guards. Everything here is plain code: the LLM cannot talk its way past any of it."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from bodhan_mcp import pricing
from bodhan_mcp.textutil import has_indic_script, normalize_digits


# ---------------------------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------------------------
@dataclass
class Budget:
    max_steps: int = 30
    max_cost_inr: float = 20.0
    call_timeout_s: float = 240.0
    max_wall_s: float = 900.0
    steps_used: int = 0
    cost_spent: float = 0.0
    reserved: float = 0.0  # estimated cost of calls currently in flight (parallel calls cannot jointly overshoot)
    started: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def exhausted_reason(self) -> str | None:
        if self.steps_used >= self.max_steps:
            return f"step budget exhausted ({self.steps_used}/{self.max_steps} steps)"
        if self.cost_spent >= self.max_cost_inr - 1e-9:
            return f"cost budget exhausted (Rs {self.cost_spent:.3f} of Rs {self.max_cost_inr:.2f})"
        if self.elapsed() >= self.max_wall_s:
            return f"time budget exhausted ({self.elapsed():.0f}s of {self.max_wall_s:.0f}s)"
        return None

    def refuse_estimate(self, estimate: float) -> str | None:
        """Refuse BEFORE spending if this call alone would push total spend over the cap."""
        if self.cost_spent + self.reserved + estimate > self.max_cost_inr + 1e-9:
            return (f"estimated cost Rs {estimate:.3f} would exceed the budget (spent Rs {self.cost_spent:.3f}, in flight Rs {self.reserved:.3f}, "
                    f"cap Rs {self.max_cost_inr:.2f}); the call was NOT made")
        return None

    def snapshot(self) -> dict:
        return {"steps_left": self.max_steps - self.steps_used, "cost_left_inr": round(self.max_cost_inr - self.cost_spent, 4),
                "seconds_left": round(self.max_wall_s - self.elapsed())}


def norm_path(p: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(p))))


def _text_len(value: str) -> int:
    p = Path(value)
    try:
        if "\n" not in value and len(value) < 400 and p.is_file():
            return len(p.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        pass
    return len(value)


def estimate_cost(tool: str, args: dict, media_seconds: dict[str, float], pages: dict[str, int]) -> float:
    """Upper-ish estimate of what a tool call will cost, from its arguments and known input metadata."""
    if tool == "transcribe_audio":
        return pricing.transcribe_cost(media_seconds.get(norm_path(args.get("audio_path", "")), 0.0))
    if tool == "verify_audio":
        return pricing.transcribe_cost(media_seconds.get(norm_path(args.get("audio_path", "")), 30.0))
    if tool == "translate_text":
        n = _text_len(str(args.get("text_or_path", "")))
        chars_per_token = 4.0 if str(args.get("target_lang", "")).lower() in ("en", "english") else 2.5
        mult = 2 if args.get("pivot_via_english") else 1
        return pricing.translate_cost(int(n / chars_per_token * 1.2)) * mult
    if tool == "synthesize_speech":
        return pricing.speak_cost(_text_len(str(args.get("text_or_path", ""))))
    if tool == "ocr_document":
        key = str(args.get("path", ""))
        return pricing.ocr_cost(pages.get(norm_path(key), 1))
    return 0.0


# ---------------------------------------------------------------------------------------------
# LLM must not write Indian-language text
# ---------------------------------------------------------------------------------------------
def find_indic_violations(args: object, *, skip_existing_paths: bool = True) -> list[str]:
    """Return the string values in `args` that contain Indic/Arabic-script text and are not paths of existing files."""
    bad: list[str] = []

    def walk(v: object) -> None:
        if isinstance(v, str):
            if has_indic_script(v):
                if skip_existing_paths and "\n" not in v and len(v) < 500:
                    try:
                        if os.path.exists(v):
                            return
                    except (OSError, ValueError):
                        pass
                bad.append(v[:60])
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(args)
    return bad


# ---------------------------------------------------------------------------------------------
# prompt-injection scan (flag, never obey)
# ---------------------------------------------------------------------------------------------
_INJECTION = [
    r"ignore (?:all |any |your |the |these |previous |prior |above |earlier )+(?:instructions|rules|prompts?|directions)",
    r"disregard (?:all |any |your |the |previous |prior |above )+(?:instructions|rules|prompts?)",
    r"forget (?:everything|all|your|the previous)",
    r"you are now\b", r"new (?:instructions|system prompt|rules)\b", r"system (?:prompt|message|override)\b",
    r"(?:reveal|print|show|output|send|leak) (?:me )?(?:your |the )?(?:api[_ -]?keys?|secrets?|system prompt|credentials|passwords?|instructions)",
    r"\b(?:api[_ -]?key|bearer token)\b.{0,40}\b(?:send|post|email|upload|reveal)\b",
    r"(?:call|run|execute|invoke) (?:the )?(?:tool|function|command|shell)",
    r"\bdo not (?:translate|follow|tell the user)\b", r"\bdelete (?:all |the )?files?\b",
    r"instead,? (?:you must|you should|please|just) ",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION), re.I)


def scan_injection(text: str) -> list[str]:
    return sorted({m.group(0).strip()[:80] for m in _INJECTION_RE.finditer(text or "")})


def wrap_untrusted(source: str, text: str) -> str:
    # neutralise anything that looks like our own delimiter inside the data
    safe = re.sub(r"</?untrusted_data[^>]*>", "[delimiter removed]", text)
    return f'<untrusted_data source="{source}">\n{safe}\n</untrusted_data>'


# ---------------------------------------------------------------------------------------------
# unsupported target language (early refusal)
# ---------------------------------------------------------------------------------------------
UNSUPPORTED_LANGS = [
    "french", "spanish", "german", "italian", "portuguese", "russian", "chinese", "mandarin", "cantonese", "japanese", "korean", "arabic",
    "turkish", "dutch", "thai", "vietnamese", "indonesian", "malay", "swahili", "greek", "hebrew", "polish", "ukrainian", "persian", "farsi",
    "swedish", "norwegian", "danish", "finnish", "czech", "romanian", "hungarian", "sinhala", "sinhalese", "tulu", "tibetan", "burmese",
]
_L = "|".join(UNSUPPORTED_LANGS)
_TARGET_PATTERNS = [
    re.compile(rf"\b(?:into|to|in|as)\s+(?:the\s+)?({_L})\b", re.I),
    re.compile(rf"\b({_L})\s+(?:subtitles?|captions?|dub(?:bing|bed)?|voice-?over|translation|narration|version)\b", re.I),
]
_GENERATIVE = re.compile(r"\b(translat|dub|subtitle|caption|voice|speak|read (?:it |this |that )?(?:aloud|out)|narrat|convert|summar|transcrib|record|say|write)", re.I)


def unsupported_target_language(task: str) -> str | None:
    """If the task asks for OUTPUT in a language no Bodhan model supports, return its name (else None)."""
    if not _GENERATIVE.search(task):
        return None
    for rx in _TARGET_PATTERNS:
        m = rx.search(task)
        if m:
            return m.group(1).capitalize()
    return None


# ---------------------------------------------------------------------------------------------
# verification gate
# ---------------------------------------------------------------------------------------------
@dataclass
class VerificationGate:
    """Tracks every generated audio file / translation and whether it has a PASSING verification."""

    audio: dict[str, dict] = field(default_factory=dict)         # audio_path -> {language, text_path}
    translations: dict[str, dict] = field(default_factory=dict)  # output_path -> {source_path, target_language}
    results: dict[str, dict] = field(default_factory=dict)       # path -> {kind, passed, detail, attempts}

    superseded: list[dict] = field(default_factory=list)         # earlier attempts replaced by a retry (kept for the report)

    def _supersede(self, table: dict[str, dict], match: dict, new_path: str, kind: str) -> None:
        for old in [p for p, meta in table.items() if p != new_path and all(meta.get(k) == v for k, v in match.items())]:
            self.superseded.append({"kind": kind, "path": old, "result": self.results.pop(old, None)})
            table.pop(old)

    def register_audio(self, path: str, language: str, text_path: str) -> None:
        self._supersede(self.audio, {"language": language, "text_path": text_path}, path, "audio")
        self.audio[path] = {"language": language, "text_path": text_path}
        self.results.pop(path, None)

    def register_translation(self, path: str, source_path: str, target_language: str) -> None:
        self._supersede(self.translations, {"source_path": source_path, "target_language": target_language}, path, "translation")
        self.translations[path] = {"source_path": source_path, "target_language": target_language}
        self.results.pop(path, None)

    def record(self, path: str, kind: str, passed: bool, detail: dict) -> None:
        """Store a verification result. Only files the gate tracks (generated audio / translations) count: a check the LLM runs on
        anything else (an input file, a no-op translation) is informational and can neither fail nor satisfy the gate."""
        if path not in self.audio and path not in self.translations:
            return
        prev = self.results.get(path, {})
        self.results[path] = {"kind": kind, "passed": passed, "detail": detail, "attempts": prev.get("attempts", 0) + 1}

    def unverified(self) -> list[tuple[str, str]]:
        out = [("audio", p) for p in self.audio if p not in self.results]
        out += [("translation", p) for p in self.translations if p not in self.results]
        return out

    def failing(self) -> list[tuple[str, str]]:
        return [(r["kind"], p) for p, r in self.results.items() if not r["passed"]]

    def all_passed(self) -> bool:
        return not self.unverified() and not self.failing()


def has_digits_only(text: str) -> bool:
    return bool(normalize_digits(text).strip().isdigit())
