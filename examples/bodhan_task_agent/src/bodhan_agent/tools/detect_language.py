from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Literal

from bodhan_agent.core import guards
from bodhan_agent.schemas.tools import DetectLanguageInput, LocalResult
from bodhan_agent.tools.base import LocalTool, LocalToolError, ToolContext, known_file
from bodhan_agent.tools.read import READABLE_KINDS, _load
from bodhan_mcp.core.languages import SCRIPT_LANGUAGES, TRANSCRIBE_LANGUAGES, TRANSLATE_TARGETS
from bodhan_mcp.utils.script import script_counts

Confidence = Literal["high", "medium", "low", "none"]

SAMPLE_CHARS = 200_000
MIXED_SHARE = 0.8
MIN_MARKERS = 3
LEAD_RATIO = 2.0
MAX_SCRIPTS = 4

MARKER_WORDS: dict[str, frozenset[str]] = {
    "hi": frozenset("है हैं का की के में से और नहीं यह था थी थे भी कि पर लिए".split()),
    "mr": frozenset("आहे आहेत आणि नाही हे या होते होता त्या मध्ये पण आम्ही".split()),
    "ne": frozenset("छ छन् हो र को मा लाई थियो गर्न पनि भने यो छैन".split()),
    "mai": frozenset("अछि छल छथि केर सँ मे आ ओ हम अहाँ".split()),
    "sa": frozenset("अस्ति च इति तत् सः एव अपि न भवति तस्य अहम्".split()),
    "kok": frozenset("आसा आसात आनी हांव जाल्यार ना तुमी म्हजो".split()),
    "bn": frozenset("এবং না এই করে হয় আমি তার থেকে জন্য ছিল আমার কিন্তু".split()),
    "as": frozenset("আৰু নহয় এই কৰে হয় মই তেওঁ পৰা বাবে আছিল মোৰ কিন্তু".split()),
    "ur": frozenset("ہے ہیں اور کے کی میں سے نہیں یہ کا تھا بھی".split()),
    "sd": frozenset("۾ ۽ کي جو جي آهي ته هن جا مان".split()),
}

MARKER_LETTERS: dict[str, frozenset[str]] = {
    "as": frozenset("ৰৱ"),
    "bn": frozenset("র"),
    "sd": frozenset("ڄڃٻڀڏڌڙڪڻڦڳڱٽڊ"),
    "ks": frozenset("ٲٳۄؠٕ"),
    "ur": frozenset("ٹڈڑےں"),
}


def _name(code: str) -> str:
    return TRANSLATE_TARGETS.get(code) or TRANSCRIBE_LANGUAGES.get(code) or code


def _is_edge(char: str) -> bool:
    return unicodedata.category(char)[0] in "PSNZC"


def _words(text: str) -> Counter[str]:
    words: Counter[str] = Counter()
    for token in text.split():
        start, end = 0, len(token)
        while start < end and _is_edge(token[start]):
            start += 1
        while end > start and _is_edge(token[end - 1]):
            end -= 1
        if start < end:
            words[token[start:end]] += 1
    return words


def _marker_scores(text: str, candidates: tuple[str, ...]) -> dict[str, dict[str, int]]:
    words = _words(text)
    letters = Counter(text)
    scores: dict[str, dict[str, int]] = {}
    for code in candidates:
        word_hits = sum(count for word, count in words.items() if word in MARKER_WORDS.get(code, ()))
        letter_hits = sum(letters[char] for char in MARKER_LETTERS.get(code, ()))
        scores[code] = {"marker_words": word_hits, "marker_letters": letter_hits}
    return scores


def _rank(scores: dict[str, dict[str, int]]) -> list[tuple[str, int]]:
    totals = [(code, item["marker_words"] + item["marker_letters"]) for code, item in scores.items()]
    return sorted(totals, key=lambda pair: (-pair[1], pair[0]))


def _shared_verdict(ranked: list[tuple[str, int]]) -> tuple[str | None, Confidence]:
    best, top = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0
    if top == 0 or top == runner:
        return None, "low"
    if top >= MIN_MARKERS and top >= LEAD_RATIO * runner:
        return best, "high"
    return best, "medium"


def _lower(confidence: Confidence) -> Confidence:
    return "medium" if confidence == "high" else confidence


class DetectLanguageTool(LocalTool):
    name = "detect_language"
    description = (
        "Give evidence about which language a text file of this run is in, computed by code from its script, "
        "common words and distinctive letters. It returns candidate language codes with a confidence level, "
        "never the text. Use it when a file's language is unknown or its script is shared by several languages "
        "(Devanagari, Bengali, Arabic) before choosing a language for translate_text or synthesize_speech. "
        "State the chosen language and the confidence as an assumption."
    )
    Input = DetectLanguageInput

    async def run(self, params: DetectLanguageInput, ctx: ToolContext) -> LocalResult:
        artifact = known_file(ctx, params.path)
        if artifact.kind not in READABLE_KINDS:
            raise LocalToolError(
                "unsupported_input",
                f"{artifact.id} is a {artifact.kind} file, not text.",
                hint="Use ocr_document for PDFs and images, or transcribe_audio for audio and video.",
            )
        text = _load(artifact)[:SAMPLE_CHARS]
        counts = script_counts(text)
        letters = sum(counts.values())
        data: dict[str, object] = {"artifact": artifact.id, "path": artifact.path, "letters": letters}
        if letters == 0:
            data.update(language=None, confidence="none", scripts=[], candidates=[], note="No letters found.")
            return LocalResult(data=data, artifact_ids=[artifact.id])
        scripts = [
            {"script": script, "share": round(count / letters, 3)} for script, count in counts.most_common(MAX_SCRIPTS)
        ]
        script, count = counts.most_common(1)[0]
        share = count / letters
        language, confidence, candidates, note = self._judge(text, script)
        if share < MIXED_SHARE:
            confidence = _lower(confidence)
            note = " ".join(part for part in (note, f"Mixed scripts: {script} is {share:.0%} of letters.") if part)
        data.update(
            language=language,
            confidence=confidence,
            script=script,
            scripts=scripts,
            candidates=candidates,
        )
        if note:
            data["note"] = note
        return LocalResult(data=data, artifact_ids=[artifact.id])

    def _judge(self, text: str, script: str) -> tuple[str | None, Confidence, list[dict[str, object]], str | None]:
        codes = SCRIPT_LANGUAGES.get(script, ())
        if script == "Latin":
            romanized = guards.romanized_indic_words(text)
            candidate = [{"language": "en", "name": _name("en")}]
            if romanized:
                return "en", "medium", candidate, "Contains words that look like romanized Indian-language text."
            return "en", "high", candidate, None
        if len(codes) == 1:
            return codes[0], "high", [{"language": codes[0], "name": _name(codes[0])}], None
        scores = _marker_scores(text, codes)
        ranked = _rank(scores)
        language, confidence = _shared_verdict(ranked)
        candidates: list[dict[str, object]] = [
            {"language": code, "name": _name(code), **scores[code]} for code, total in ranked if total > 0
        ]
        unscored = [code for code, total in ranked if total == 0]
        note = f"{script} is shared by {', '.join(codes)}."
        if unscored:
            note += f" No evidence for {', '.join(unscored)}."
        if language is None:
            note += " Ask for or assume a language and state it."
        return language, confidence, candidates, note
