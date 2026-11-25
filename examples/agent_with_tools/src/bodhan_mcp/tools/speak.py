"""Indic-Speak client: text-to-speech into a WAV file. Voices come from the fixed library (no cloning).

The model wants a sentence or two per request, so text is split with the shared sentence splitter and
the returned WAV clips (PCM16, 24 kHz, mono) are concatenated into one file. Billed Rs 6 per 10,000 input chars.
"""

from __future__ import annotations

import json
import os
import wave
from pathlib import Path

import httpx
from pydantic import BaseModel

from bodhan_core.audio import join_wavs
from bodhan_core.cache import ResultCache
from bodhan_core.chunking import split_text
from bodhan_core.client import post
from bodhan_core.config import api_key, section
from bodhan_core.errors import ToolError
from bodhan_core.langid import detect_language
from bodhan_core.runtime import BACKOFF_S, make_client
from bodhan_core.textio import resolve_text
from bodhan_mcp.tools.files import output_root

_CFG = section("speak")
INR_PER_CHAR = _CFG["inr_per_10k_chars"] / 10_000
MODEL = "indic-speak"
MAX_CHARS_PER_REQUEST = _CFG["max_chars_per_request"]
MAX_TOTAL_CHARS = _CFG["max_total_chars"]

# From scripts/bodhan_api_rules.json: language -> (female voices, male voices).
VOICES: dict[str, tuple[list[str], list[str]]] = {
    "as": (["Prastuti"], ["Ankur"]), "bn": (["Ishita"], ["Sourav"]), "brx": (["Gwrbw"], ["Sansuma"]),
    "doi": (["Preeti"], ["Sham"]), "gu": (["Dhara"], ["Parth"]), "hi": (["Kavya", "Suhani"], ["Amit"]),
    "kn": (["Deepika"], ["Adarsh"]), "kok": (["Anjali"], ["Sandeep"]), "ks": (["Zoon"], ["Ishfaq"]),
    "mai": (["Vaidehi"], ["Madhukar"]), "ml": (["Lakshmi"], ["Kiran"]), "mni": (["Thoibi"], ["Chaoba"]),
    "mr": (["Anagha"], ["Chinmay"]), "ne": (["Srijana"], ["Sagar"]), "or": (["Itishree"], ["Akash"]),
    "pa": (["Kaur"], ["Manpreet"]), "sa": (["Bharati"], ["Aryaman"]), "sat": (["Phulmani"], ["Sibu"]),
    "sd": (["Moomal"], ["Rano"]), "ta": (["Anitha"], ["Arun"]), "te": (["Sravani"], ["Vamsi"]),
    "ur": (["Saba"], ["Zaid"]),
    "en": (["Kavya"], ["Amit"]),  # no English voices are listed; the API docs show Hindi voice "Amit" speaking English
}  # fmt: skip
SPEAK_LANGUAGES = set(VOICES)

_CACHE: ResultCache[bytes] = ResultCache()  # (text, lang, voice, style) -> wav bytes


class SpeakResult(BaseModel):
    ok: bool
    audio_path: str | None = None
    voice: str | None = None
    duration_s: float = 0.0
    chunks: int = 0
    characters: int = 0
    cost_inr: float = 0.0
    language: str | None = None
    notice: str | None = None
    error: ToolError | None = None


def _fail(code: str, message: str, retryable: bool = False, cost_inr: float = 0.0) -> SpeakResult:
    return SpeakResult(ok=False, cost_inr=cost_inr, error=ToolError(code=code, message=message, retryable=retryable))  # cost_inr: chunks already billed


def pick_voice(language: str, gender: str) -> str | None:
    female, male = VOICES[language]
    pool = female if gender == "female" else male
    return pool[0] if pool else None


async def speak(
    text: str | None,
    language: str | None,
    output_path: str,
    gender: str = "female",
    voice: str | None = None,
    style: str | None = None,
    text_path: str | None = None,
    regenerate: bool = False,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    backoff_s: float = BACKOFF_S,
) -> SpeakResult:
    resolved = resolve_text(text, text_path)
    if isinstance(resolved, ToolError):
        return SpeakResult(ok=False, error=resolved)
    text = resolved
    detected = None
    if language is None:  # identify it from the script
        found = detect_language(text)
        detected = found.language if found else None
        language = detected
        if language is None:
            return _fail("language_undetected", "Could not detect the language of `text` (no letters found). Pass `language`.")
    if language not in SPEAK_LANGUAGES:
        return _fail("unsupported_language", f"`{language}` is not supported. Use one of: {', '.join(sorted(SPEAK_LANGUAGES))}.")
    if gender not in ("female", "male"):
        return _fail("bad_gender", f"`gender` is `{gender}`; it must be 'female' or 'male'.")
    if Path(output_path).suffix.lower() != ".wav":
        return _fail("bad_output_path", f"`output_path` `{output_path}` must end in .wav because the tool writes a WAV file. Example: 'outputs/speech.wav'.")
    if os.environ.get("BODHAN_OUTPUT_ROOT"):  # set by the agent: text from untrusted inputs must not choose where files land
        root = output_root()
        target = Path(output_path)
        target = (target if target.is_absolute() else root / target).resolve()
        if not target.is_relative_to(root):
            return _fail("path_outside_output_root", f"Refusing to write `{target}`: audio may only be written under `{root}`. Pass a path inside that folder.")
        output_path = str(target)
    if len(text) > MAX_TOTAL_CHARS:
        return _fail("text_too_long", f"Input has {len(text)} characters; the limit per call is {MAX_TOTAL_CHARS}. Split the text and call again.")
    voice = voice or pick_voice(language, gender)
    if not voice:
        return _fail("no_voice", f"No `{gender}` voice is listed for `{language}`. Pass `voice` explicitly or try the other gender.")
    key = api_key("BODHAN_SPEAK_KEY")
    if not key:
        return _fail("missing_api_key", "BODHAN_SPEAK_KEY (or BODHAN_API_KEY) is not set in the server environment.")

    instructions: dict = {"lang": language}
    if style:
        instructions["style"] = style
    pieces = [p.strip() for p in split_text(text, MAX_CHARS_PER_REQUEST) if p.strip()]

    clips: list[bytes] = []
    billed_chars = 0
    async with make_client(transport=transport) as client:
        for i, piece in enumerate(pieces, 1):
            cache_key = (piece, language, voice, style)
            if cache_key in _CACHE and not regenerate:  # regenerate=True is for a retry after a bad take: the cache would return the same audio
                clips.append(_CACHE[cache_key])
                continue
            body = {"model": MODEL, "input": piece, "voice": voice, "instructions": json.dumps(instructions)}
            res = await post(client, "/v1/audio/speech", key, backoff_s, json=body)
            if isinstance(res, ToolError):
                return _fail(res.code, f"Chunk {i} of {len(pieces)} failed ({len(clips)} synthesized before it): {res.message}", res.retryable, round(billed_chars * INR_PER_CHAR, 6))
            _CACHE[cache_key] = res.content
            clips.append(res.content)
            billed_chars += len(piece)

    try:
        wav, seconds = join_wavs(clips)
    except (wave.Error, EOFError) as exc:
        return _fail("bad_response", f"Bodhan returned audio that is not a valid WAV: {exc}. Retry the call; if it persists, shorten the text.", True, round(billed_chars * INR_PER_CHAR, 6))
    out = Path(output_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(wav)
    except OSError as exc:
        return _fail("output_not_writable", f"Could not write `{out}`: {exc.strerror or exc}. Choose a writable `output_path`; no credits were refunded for the synthesis.", cost_inr=round(billed_chars * INR_PER_CHAR, 6))
    out = out.resolve()
    return SpeakResult(
        ok=True,
        audio_path=str(out),
        voice=voice,
        language=language,
        duration_s=round(seconds, 2),
        chunks=len(pieces),
        characters=len(text),
        cost_inr=round(billed_chars * INR_PER_CHAR, 6),
        notice="AI-generated audio (Bodhan Indic-Speak).",
    )
