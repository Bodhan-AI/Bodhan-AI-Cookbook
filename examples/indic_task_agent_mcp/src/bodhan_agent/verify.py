"""Deterministic verification of delivered artifacts, run before the run may be called complete.

#Plan
- Every delivered file must exist and be non-empty.
- Audio must come from synthesize_speech with back-transcription on, and no chunk may still be flagged.
- Translations must keep every number, be mostly in the target script, and not be truncated.
- SRT files must parse with increasing timestamps. A duration named in the task ("two-minute") is checked ±25%.
- The status the agent reports comes from these checks, not from the LLM's own claim.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bodhan_mcp import audio, langs


@dataclass
class Check:
    artifact: str
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Verification:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def as_list(self) -> list[dict]:
        return [asdict(c) for c in self.checks]


SRT_TS = re.compile(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)")


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def check_srt(path: Path) -> Check:
    text = path.read_text(encoding="utf-8", errors="ignore")
    stamps = SRT_TS.findall(text)
    if not stamps:
        return Check(path.name, "srt_valid", False, "no cues found")
    last = -1.0
    for st in stamps:
        a, b = _secs(*st[:4]), _secs(*st[4:])
        if b < a or a < last - 0.01:
            return Check(path.name, "srt_valid", False, f"timestamps out of order near {a:.2f}s")
        last = a
    return Check(path.name, "srt_valid", True, f"{len(stamps)} cues")


def verify_artifacts(artifacts: list[Path], tool_records: dict[str, dict], target_languages: list[str],
                     duration_target_s: float | None) -> Verification:
    """tool_records maps an output file's resolved path to the tool result that produced it."""
    v = Verification()
    audio_durations: list[float] = []
    for art in artifacts:
        name = art.name
        if not art.exists() or art.stat().st_size == 0:
            v.checks.append(Check(name, "exists", False, "missing or empty"))
            continue
        v.checks.append(Check(name, "exists", True, f"{art.stat().st_size} bytes"))
        rec = tool_records.get(str(art.resolve()), {})
        tool, result = rec.get("tool"), rec.get("result", {})
        ext = art.suffix.lower()
        if ext in (".mp3", ".wav", ".ogg", ".m4a"):
            info = audio.probe(art)
            if info.get("duration_s"):
                audio_durations.append(info["duration_s"])
            ver = result.get("verification") if tool == "synthesize_speech" else None
            if not ver:
                v.checks.append(Check(name, "speech_verified", False, "audio was not produced by synthesize_speech with verification"))
            elif not ver.get("checked"):
                v.checks.append(Check(name, "speech_verified", False, "back-transcription was turned off (verify=false)"))
            else:
                flagged = ver.get("chunks_flagged", 0)
                v.checks.append(Check(name, "speech_verified", flagged == 0,
                                      f"mean CER {ver.get('mean_cer')}, max CER {ver.get('max_cer')}, {flagged} chunk(s) flagged"))
        elif ext == ".srt":
            v.checks.append(check_srt(art))
        elif ext in (".md", ".txt"):
            text = art.read_text(encoding="utf-8", errors="ignore")
            if tool == "translate_text":
                missing = result.get("missing_numbers") or []
                v.checks.append(Check(name, "numbers_preserved", not missing, f"missing: {missing}" if missing else "all numbers kept"))
                warn = [w for w in result.get("warnings", []) if "truncated" in w or "shorter than the source" in w]
                v.checks.append(Check(name, "complete", not warn, "; ".join(warn) or "no truncation, length plausible"))
                lang = result.get("target_language")
                if lang and result.get("script") == "native":
                    share = langs.script_matches(text, lang)
                    v.checks.append(Check(name, "target_script", share >= 0.6, f"{share:.0%} of letters in {langs.name_of(lang)} script"))
            elif tool is None and target_languages:
                # A text file the agent wrote itself must not be the Indian-language deliverable.
                indic_targets = [t for t in target_languages if t != "en"]
                if indic_targets and langs.has_indic(text):
                    v.checks.append(Check(name, "provenance", False, "Indian-language text not produced by a Bodhan tool"))
    if duration_target_s and audio_durations:
        total = sum(audio_durations)
        ok = abs(total - duration_target_s) <= 0.25 * duration_target_s
        v.checks.append(Check("audio", "duration_target", ok, f"{total:.0f}s delivered vs {duration_target_s:.0f}s asked (±25%)"))
    return v
