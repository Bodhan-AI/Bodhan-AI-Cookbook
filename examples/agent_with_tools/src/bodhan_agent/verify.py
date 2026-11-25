"""Output verification: deterministic comparisons only (the model calls happen in the graph's verify node).

* Audio is verified by back-transcription: the generated WAV is transcribed with Indic-Transcribe and
  compared with the text that was spoken. This catches Indic-Speak repeating or dropping content.
* Translations are checked for coverage: not truncated, same paragraph/table-row structure, plausible
  length, and written in the requested script.
"""

from __future__ import annotations

import difflib
import re

from bodhan_core.langid import detect_language

AUDIO_MIN_SIMILARITY = 0.60  # character-level; ASR spelling differs from the source even when the audio is right
AUDIO_LENGTH_RATIO = (0.6, 1.6)  # transcript length / spoken text length; above => repeats, below => omissions
TRANSLATION_LENGTH_RATIO = (0.25, 4.0)  # translation length / source length (scripts differ a lot in length)


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.lower(), flags=re.UNICODE).strip()


def compare_spoken(spoken_text: str, transcript: str) -> dict:
    """Compare the text sent to TTS with what the audio says (transcript). Returns {passed, similarity, length_ratio, problems}."""
    a, b = _norm(spoken_text), _norm(transcript)
    if not a:
        return {"passed": False, "similarity": 0.0, "length_ratio": 0.0, "problems": ["no spoken text to compare"]}
    if not b:
        return {"passed": False, "similarity": 0.0, "length_ratio": 0.0, "problems": ["the back-transcript is empty: the audio may be silent"]}
    similarity = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    ratio = len(b) / len(a)
    problems = []
    if ratio > AUDIO_LENGTH_RATIO[1]:
        problems.append(f"transcript is {ratio:.1f}x longer than the text: the audio likely repeats content")
    if ratio < AUDIO_LENGTH_RATIO[0]:
        problems.append(f"transcript is only {ratio:.0%} of the text length: the audio likely omits content")
    if similarity < AUDIO_MIN_SIMILARITY:
        problems.append(f"transcript matches the text only {similarity:.0%} (needs {AUDIO_MIN_SIMILARITY:.0%})")
    return {"passed": not problems, "similarity": round(similarity, 3), "length_ratio": round(ratio, 3), "problems": problems}


def _paragraphs(text: str) -> int:
    """Blank-line separated paragraphs. Hard-wrapped lines inside a paragraph (typical OCR output) do not count."""
    return len([p for p in re.split(r"\n\s*\n", text) if p.strip()])


_STRUCTURAL = re.compile(r"\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s)")


def _list_lines(text: str) -> int:
    """Headings and list items: lines whose count a translation must preserve."""
    return len([ln for ln in text.splitlines() if _STRUCTURAL.match(ln)])


def _table_rows(text: str) -> int:
    return len([ln for ln in text.splitlines() if ln.strip().startswith("|")])


def check_translation(source: str, translation: str, target_language: str, *, truncated: bool, script: str | None = None) -> dict:
    """Coverage checks for one translate_text call. Returns {passed, problems}."""
    problems = []
    if truncated:
        problems.append("the tool reported the translation as truncated (incomplete)")
    if not translation.strip():
        problems.append("the translation is empty")
        return {"passed": False, "problems": problems}
    if source.strip():
        ratio = len(translation) / len(source)
        if not TRANSLATION_LENGTH_RATIO[0] <= ratio <= TRANSLATION_LENGTH_RATIO[1]:
            problems.append(f"translation length is {ratio:.2f}x the source: content may be missing or duplicated")
        if _paragraphs(translation) != _paragraphs(source):
            problems.append(f"source has {_paragraphs(source)} paragraphs but the translation has {_paragraphs(translation)}")
        if _list_lines(translation) != _list_lines(source):
            problems.append(f"source has {_list_lines(source)} heading/list lines but the translation has {_list_lines(translation)}")
        if _table_rows(source) != _table_rows(translation):
            problems.append(f"source has {_table_rows(source)} table rows but the translation has {_table_rows(translation)}")
    if script in (None, "native"):
        found = detect_language(translation)
        if found is not None and target_language.split("-")[0] not in {found.language, *(c.split("-")[0] for c in found.candidates)}:
            problems.append(f"translation is written in the {found.script} script, which is not used for '{target_language}'")
    return {"passed": not problems, "problems": problems}


_CUE_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{3})")


def _secs(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def check_srt(srt: str, target_language: str | None, segments_in: int) -> dict:
    """Structural checks on a generated SRT file: it has cues, every cue has text, times are valid and in order,
    it covers every spoken segment, and (when translated) the text is in the target language's script."""
    problems: list[str] = []
    blocks = [b for b in re.split(r"\n\s*\n", srt.strip()) if b.strip()]
    texts: list[str] = []
    prev_start = -1.0
    bad_time = out_of_order = empty = 0
    for block in blocks:
        lines = block.splitlines()
        m = _CUE_TIME.search(lines[1]) if len(lines) > 1 else None
        if not m:
            bad_time += 1
            continue
        start, end = _secs(*m.groups()[:4]), _secs(*m.groups()[4:])
        bad_time += end <= start
        out_of_order += start < prev_start
        prev_start = start
        body = " ".join(lines[2:]).strip()
        empty += not body
        texts.append(body)
    if not blocks:
        return {"passed": False, "problems": ["the SRT file has no cues"]}
    if bad_time:
        problems.append(f"{bad_time} cue(s) have an invalid time range")
    if out_of_order:
        problems.append(f"{out_of_order} cue(s) start before the previous one")
    if empty:
        problems.append(f"{empty} cue(s) have no text")
    if len(blocks) < segments_in:
        problems.append(f"{len(blocks)} cues for {segments_in} spoken segments: some speech is not covered")
    if target_language and texts:
        found = detect_language(" ".join(texts))
        if found is not None and target_language.split("-")[0] not in {found.language, *(c.split("-")[0] for c in found.candidates)}:
            problems.append(f"subtitles are written in the {found.script} script, which is not used for '{target_language}'")
    return {"passed": not problems, "problems": problems}
