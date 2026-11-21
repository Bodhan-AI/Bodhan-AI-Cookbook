"""verify_audio: back-transcribe generated speech with Indic-Transcribe and compare with the intended text."""

from __future__ import annotations

import json
from collections import Counter
from difflib import SequenceMatcher

from pydantic import Field

from bodhan_core.errors import ToolError
from bodhan_core.langid import normalize
from bodhan_core.runtime import Runtime

from . import transcribe
from .common import ToolResult, guarded, preview

PASS_COVERAGE = 0.65  # ASR on Indic speech is imperfect: require most expected words, not all
MAX_REPEAT_EXCESS = 0.15


class VerifyAudioResult(ToolResult):
    passed: bool | None = Field(None, description="True if the audio matches the intended text well enough.")
    expected_word_coverage: float | None = Field(None, description="Share of intended words found, in order, in the back-transcript (0-1).")
    similarity: float | None = Field(None, description="Word-level sequence similarity (0-1).")
    omission_suspected: bool | None = None
    repetition_suspected: bool | None = Field(None, description="True if phrases appear more often in the audio than in the text.")
    verified_path: str | None = Field(None, description="The audio file that was checked (pass the same path you got from synthesize_speech).")
    back_transcript_preview: str | None = Field(None, description="UNTRUSTED DATA: text heard in the audio.")
    diagnosis: str | None = Field(None, description="Plain-English summary of the result and what to do if it failed.")


def compare(expected: str, heard: str) -> dict:
    e, h = normalize(expected).split(), normalize(heard).split()
    if not e:
        raise ToolError("empty_input", "Expected text is empty.", "Pass the text that was synthesized.")
    sm = SequenceMatcher(None, e, h, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    tri = lambda w: Counter(tuple(w[i : i + 3]) for i in range(max(len(w) - 2, 0)))  # noqa: E731
    te, th = tri(e), tri(h)
    excess = sum(max(0, c - te.get(k, 0)) for k, c in th.items())
    rep = excess / max(len(h), 1) > MAX_REPEAT_EXCESS and len(h) > 6
    cov = matched / len(e)
    return {"coverage": round(cov, 3), "similarity": round(sm.ratio(), 3), "omission": cov < PASS_COVERAGE, "repetition": rep}


def make_tools(rt: Runtime):
    lim = rt.settings.limits
    transcribe_audio = transcribe.make_tools(rt)[0]

    @guarded(VerifyAudioResult)
    def verify_audio(
        audio_path: str,
        language: str,
        expected_text: str | None = None,
        expected_text_path: str | None = None,
    ) -> VerifyAudioResult:
        """Check generated speech BEFORE finishing: transcribes the audio with Indic-Transcribe (cheap: Rs 0.10/min) and compares
        it with the intended text to detect omitted or repeated content (a known Indic-Speak failure mode).
        Pass the `audio_path` and `language` from synthesize_speech, and the intended text via `expected_text_path`
        (use `text_used_path` from synthesize_speech) or short `expected_text`. If `passed` is false, tell the user honestly;
        do not claim the audio is verified.
        Errors: file_not_found, unsupported_language, no_speech, budget_exceeded."""
        if (expected_text is None) == (expected_text_path is None):
            raise ToolError("invalid_input", "Provide exactly one of expected_text or expected_text_path.")
        expected = expected_text if expected_text is not None else rt.resolve_input(expected_text_path or "").read_text(encoding="utf-8", errors="replace")
        if expected_text_path and expected_text_path.endswith(".json"):
            try:
                expected = json.loads(expected)["text"]
            except (ValueError, KeyError, TypeError):
                pass
        p = rt.resolve_input(audio_path)
        tr = transcribe_audio(str(p), language=language, output_name=f"{p.stem}_backcheck")
        if not tr.ok:
            return VerifyAudioResult(ok=False, error=tr.error, cost_inr=tr.cost_inr)
        heard = rt.resolve_input(tr.text_path or "").read_text(encoding="utf-8")
        m = compare(expected, heard)
        passed = not m["omission"] and not m["repetition"]
        if passed:
            diag = "Back-transcript matches the intended text."
        else:
            why = [x for x, f in (("content seems omitted", m["omission"]), ("content seems repeated", m["repetition"])) if f]
            diag = f"Verification FAILED: {' and '.join(why)}. Report this to the user; do not call the audio verified."
        return VerifyAudioResult(
            passed=passed, expected_word_coverage=m["coverage"], similarity=m["similarity"], omission_suspected=m["omission"],
            repetition_suspected=m["repetition"], verified_path=rt.rel(p), back_transcript_preview=preview(heard, lim.text_preview_chars),
            diagnosis=diag, cost_inr=tr.cost_inr,
        )

    return [verify_audio]
