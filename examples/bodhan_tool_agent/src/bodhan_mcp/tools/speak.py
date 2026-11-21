"""synthesize_speech: Indic-Speak with sentence-level chunking, fixed voice library and AI-generated labelling."""

from __future__ import annotations

import array
import re
from typing import Literal

from pydantic import Field

from bodhan_core import audio
from bodhan_core.errors import ToolError
from bodhan_core.runtime import Runtime

from .. import services
from ..library import NAMES, STYLES, TTS_LANGUAGES, VOICES, check_language
from .common import ToolResult, guarded
from .translate import split_long

AI_LABEL = "AI-generated speech (Bodhan Indic-Speak). Not a recording of a real person."
_SPACE = re.compile(r"[ \t]+")


class SpeakResult(ToolResult):
    audio_path: str | None = Field(None, description="Generated audio file. Pass to verify_audio before finishing.")
    format: str | None = None
    language: str | None = None
    voice: str | None = None
    style: str | None = None
    duration_s: float | None = None
    requests: int | None = Field(None, description="How many Indic-Speak requests (one per short sentence group).")
    chars: int | None = None
    ai_generated: bool = Field(True, description="Always true. Tell the user this audio is AI-generated.")
    label: str = Field(AI_LABEL, description="Label to show next to the audio.")
    text_used_path: str | None = Field(None, description="The exact text that was synthesized (for verify_audio).")


def pick_voice(lang: str, voice: str | None, gender: str | None) -> tuple[str, str]:
    lib = VOICES.get(lang)
    if not lib:
        raise ToolError("unsupported_language", f"No Indic-Speak voices for '{lang}'.", "Supported: " + ", ".join(TTS_LANGUAGES))
    if voice:
        for g, names in lib.items():
            for n in names:
                if n.lower() == voice.lower():
                    return n, g
        allv = [n for names in lib.values() for n in names]
        raise ToolError("unknown_voice", f"Voice '{voice}' is not in the {NAMES.get(lang, lang)} library.", "Voices: " + ", ".join(allv) + ". Voices are fixed; cloning is not supported.")
    g = (gender or "female").lower()
    if g not in lib:
        raise ToolError("invalid_input", f"gender must be 'female' or 'male', got '{gender}'.", "Use female or male, or pick a voice name.")
    return lib[g][0], g


def speakable_chunks(text: str, max_chars: int) -> list[str]:
    """Whitespace-normalise, then split into short sentence groups <= max_chars (TTS inputs must stay short)."""
    paras = [p for p in (_SPACE.sub(" ", x).strip() for x in re.split(r"\n+", text)) if p]
    out: list[str] = []
    for p in paras:
        out.extend(split_long(p, max_chars))
    return [c for c in out if re.search(r"\w", c)]


def make_tools(rt: Runtime):
    lim = rt.settings.limits

    @guarded(SpeakResult)
    def synthesize_speech(
        language: str,
        text: str | None = None,
        text_path: str | None = None,
        voice: str | None = None,
        gender: Literal["female", "male"] | None = None,
        style: str | None = None,
        format: Literal["wav", "mp3"] = "wav",
        output_name: str | None = None,
    ) -> SpeakResult:
        """Text-to-speech with Indic-Speak (Rs 6 per 10,000 characters: the most expensive tool, so keep text tight and call it ONCE).
        The text must already be in the target `language` (use translate_text first if it is not); give `text` (short English only)
        or `text_path` (a translation/transcript file). Long text is split into short sentence groups (<=220 chars each),
        synthesized separately and joined with short pauses. Output is labelled AI-generated (file metadata when ffmpeg exists,
        plus a sidecar .ai-generated.txt).
        `voice`: a name from list_voices (fixed library, no cloning) or `gender` female/male (default female). `style`: optional,
        from list_voices (e.g. 'news', 'educational lecture'). `format`: wav (default) or mp3 (needs ffmpeg).
        Always run verify_audio on the result: Indic-Speak sometimes repeats or omits content.
        Errors: unsupported_language, unknown_voice, empty_input, budget_exceeded, rate_limited."""
        lang = check_language(language, TTS_LANGUAGES, "speech synthesis")
        if (text is None) == (text_path is None):
            raise ToolError("invalid_input", "Provide exactly one of `text` or `text_path`.")
        if style and style not in STYLES:
            raise ToolError("invalid_input", f"Unknown style '{style}'.", "Styles: " + "; ".join(STYLES))
        if format == "mp3" and not audio.have_ffmpeg():
            raise ToolError("ffmpeg_missing", "mp3 output needs ffmpeg, which is not installed.", "Use format='wav' or install ffmpeg.")
        voice_name, _ = pick_voice(lang, voice, gender)
        raw = text if text is not None else rt.resolve_input(text_path or "").read_text(encoding="utf-8", errors="replace")
        if text_path and text_path.endswith(".json"):
            import json

            try:
                raw = json.loads(raw)["text"]
            except (ValueError, KeyError, TypeError):
                pass
        chunks = speakable_chunks(raw, lim.tts_max_chars)
        if not chunks:
            raise ToolError("empty_input", "No speakable text.", "Provide text with letters or digits.")
        total_chars = sum(len(c) for c in chunks)

        cost, pcm, rate = 0.0, array.array("h"), 24_000
        pause = None
        for i, c in enumerate(chunks):
            blob, cc = services.tts_chunk(rt, c, voice_name, lang, style)
            cost += cc
            s, rate = audio.wav_bytes_to_pcm(blob)
            if pause is None:
                pause = array.array("h", [0] * int(rate * 0.25))
            if i:
                pcm.extend(pause)
            pcm.extend(s)
        duration = len(pcm) / rate

        out_wav = rt.artifact_path(output_name or f"speech_{lang}", "speech", ".mp3" if format == "mp3" else ".wav")
        tmp_wav = out_wav.with_suffix(".tmp.wav") if format == "mp3" else out_wav
        tmp_wav.write_bytes(audio.pcm_to_wav_bytes(pcm, rate))
        if format == "mp3" or audio.have_ffmpeg():
            tagged = out_wav.with_suffix(".tagged" + out_wav.suffix)
            if audio.encode_with_metadata(tmp_wav, tagged, AI_LABEL):
                tagged.replace(out_wav)
                if tmp_wav != out_wav:
                    tmp_wav.unlink(missing_ok=True)
            elif format == "mp3":
                raise ToolError("encode_failed", "ffmpeg failed to encode mp3.", "Use format='wav'.")
        out_wav.with_name(out_wav.name + ".ai-generated.txt").write_text(AI_LABEL + f"\nvoice={voice_name} language={lang}\n", encoding="utf-8")
        used = out_wav.with_name(out_wav.stem + ".text.txt")
        used.write_text("\n".join(chunks), encoding="utf-8")
        res = SpeakResult(
            audio_path=rt.rel(out_wav), format=out_wav.suffix.lstrip("."), language=lang, voice=voice_name, style=style,
            duration_s=round(duration, 2), requests=len(chunks), chars=total_chars, cost_inr=round(cost, 4), text_used_path=rt.rel(used),
        )
        # Cheap sanity check: ~8-30 chars/second is normal speech; far outside suggests omitted or looping audio.
        cps = total_chars / duration if duration else 0
        if duration and not (4 <= cps <= 40):
            res.warnings.append(f"Unusual speech rate ({cps:.0f} chars/s): content may be omitted or repeated. Run verify_audio.")
        return res

    return [synthesize_speech]
