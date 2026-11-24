"""synthesize_speech: text of any length -> one audio file via Indic-Speak, verified by back-transcription.

Indic-Speak is guided to one or two sentences (~30 s) per request, so text is split at sentence
(then clause) boundaries, each piece is synthesised, and the clips are joined with short pauses.
With verify=true every clip is transcribed back with Indic-Transcribe and compared with its input,
which catches the repeats and omissions Bodhan warns about. A failing clip is re-synthesised as
two smaller pieces (a different input, so the cache cannot hand back the same bad audio).
"""

from __future__ import annotations

import asyncio

from ..catalog import TTS_LANGS, TTS_STYLES, pick_voice, require_language
from ..errors import BodhanToolError
from ..media import concat_wavs, encode_audio, read_text_file, read_wav, resolve_input
from ..models import ChunkCheck, SpeakResult, SpeechVerification
from ..text import (
    LANGUAGE_SCRIPT,
    chunk_for_speech,
    has_repetition,
    length_ratio,
    script_share,
    similarity,
    speakable,
    split_in_two,
)
from ..usage import speak_cost, transcribe_cost
from .common import get_context, new_ledger, output_dir, output_path, report_spend_on_error

MAX_CHARS = 30_000  # ₹18 of synthesis; anything larger is almost certainly a mistake
PASS_SIMILARITY = 0.55
RATIO_RANGE = (0.5, 1.8)
EST_CHARS_PER_SECOND = 12.0


def _check(index: int, source: str, heard: str) -> ChunkCheck:
    sim = similarity(source, heard)
    ratio = length_ratio(source, heard)
    repeated = has_repetition(heard) and not has_repetition(source)
    passed = sim >= PASS_SIMILARITY and RATIO_RANGE[0] <= ratio <= RATIO_RANGE[1] and not repeated
    return ChunkCheck(
        index=index,
        text_chars=len(source),
        similarity=round(sim, 3),
        length_ratio=round(ratio, 3),
        repeated=repeated,
        passed=passed,
    )


@report_spend_on_error
async def synthesize_speech(
    language: str,
    text: str | None = None,
    input_path: str | None = None,
    voice: str | None = None,
    gender: str | None = None,
    style: str | None = None,
    format: str = "mp3",
    verify: bool = True,
    output_dir_: str | None = None,
    max_cost_inr: float | None = None,
) -> SpeakResult:
    lang = require_language(language, TTS_LANGS, "speech synthesis")
    if format not in {"wav", "mp3", "ogg"}:
        raise BodhanToolError("invalid_input", f"format must be wav, mp3 or ogg, got {format!r}.")
    if style:
        match = next((s for s in TTS_STYLES if s.lower() == style.strip().lower()), None)
        if not match:
            raise BodhanToolError(
                "unsupported_voice",
                f"Style {style!r} is not available.",
                hint=f"Styles: {', '.join(TTS_STYLES)}. Omit style for a neutral reading.",
            )
        style = match
    voice_name = pick_voice(lang, voice, gender)
    if bool(text) == bool(input_path):
        raise BodhanToolError("invalid_input", "Pass exactly one of `text` or `input_path`.")
    stem = "speech"
    if input_path:
        src = resolve_input(input_path)
        if src.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".wav", ".mp3", ".mp4"}:
            raise BodhanToolError(
                "unsupported_file_type",
                f"{src.name} is not a text file.",
                hint="Extract text first (ocr_document / transcribe_audio).",
            )
        stem = src.stem
        raw = read_text_file(src)
    else:
        raw = text or ""
    body = speakable(raw)
    if not body.strip():
        raise BodhanToolError("invalid_input", "There is no text to speak.")
    if len(body) > MAX_CHARS:
        raise BodhanToolError(
            "invalid_input",
            f"Text is {len(body)} characters; the limit per call is {MAX_CHARS}.",
            hint="Summarise or split the text first.",
        )
    expected = LANGUAGE_SCRIPT.get(lang, "Latin")
    share = script_share(body, expected)
    if share < 0.5:
        raise BodhanToolError(
            "script_mismatch",
            f"The text is not written in {expected} script (only {share:.0%} is), so it cannot be read as {lang}.",
            hint=f"Translate it to {lang} with translate_text first, then pass that output_path here.",
        )

    ledger = new_ledger(max_cost_inr)
    client = get_context().client
    chunks = chunk_for_speech(body)
    est_seconds = len(body) / EST_CHARS_PER_SECOND
    ledger.reserve(
        speak_cost(len(body)) + (transcribe_cost(est_seconds * 1.3) if verify else 0.0),
        f"Synthesising {len(body)} characters",
    )

    async def synth(piece: str) -> bytes:
        return await client.speak(piece, voice=voice_name, language=lang, style=style, ledger=ledger)

    async def hear(wav: bytes) -> str | None:
        samples, rate = read_wav(wav)
        seconds = len(samples) / rate
        if seconds > 29.5:
            return None
        return await client.transcribe(wav, seconds=seconds, language=lang, ledger=ledger, filename="tts_check.wav")

    wavs = list(await asyncio.gather(*(synth(c) for c in chunks)))
    warnings: list[str] = []
    verification = None
    if verify:
        heard = await asyncio.gather(*(hear(w) for w in wavs), return_exceptions=True)
        checks: list[ChunkCheck] = []
        for i, (chunk, h) in enumerate(zip(chunks, heard, strict=True)):
            if isinstance(h, BodhanToolError):
                warnings.append(f"Chunk {i} could not be verified ({h.code}).")
                checks.append(
                    ChunkCheck(
                        index=i, text_chars=len(chunk), similarity=0, length_ratio=0, repeated=False, passed=False
                    )
                )
            elif isinstance(h, BaseException):
                raise h
            elif h is None:
                warnings.append(f"Chunk {i} is longer than 30 s and was not verified.")
                checks.append(
                    ChunkCheck(
                        index=i, text_chars=len(chunk), similarity=0, length_ratio=0, repeated=False, passed=False
                    )
                )
            else:
                checks.append(_check(i, chunk, h))
        # Repair: re-synthesise each failing chunk as two smaller (different) inputs and re-check.
        for c in [c for c in checks if not c.passed and c.similarity > 0]:
            halves = split_in_two(chunks[c.index])
            if len(halves) < 2:
                continue
            try:
                new_wavs = list(await asyncio.gather(*(synth(h) for h in halves)))
                new_heard = await asyncio.gather(*(hear(w) for w in new_wavs))
            except BodhanToolError as exc:
                warnings.append(f"Repair of chunk {c.index} failed: {exc.code}.")
                continue
            sub = [_check(c.index, h, t or "") for h, t in zip(halves, new_heard, strict=True)]
            if all(s.passed for s in sub):
                joined, _ = concat_wavs(new_wavs, gap_s=0.15)
                wavs[c.index] = joined
                c.passed, c.repaired = True, True
                c.similarity = round(min(s.similarity for s in sub), 3)
        failed = [c for c in checks if not c.passed]
        if failed:
            warnings.append(
                f"{len(failed)} of {len(checks)} chunk(s) did not pass back-transcription; listen to them before use."
            )
        verification = SpeechVerification(
            checked=len(checks),
            passed=len(checks) - len(failed),
            failed=len(failed),
            repaired=sum(c.repaired for c in checks),
            mean_similarity=round(sum(c.similarity for c in checks) / max(len(checks), 1), 3),
            all_passed=not failed,
            chunks=checks,
        )

    joined, duration = concat_wavs(wavs)
    out_dir = output_dir(output_dir_)
    fp = {"text": body, "lang": lang, "voice": voice_name, "style": style, "fmt": format}
    dest = output_path(out_dir, stem, f"{lang}.{voice_name.lower()}", format, fp)
    await asyncio.to_thread(encode_audio, joined, dest, format, f"{stem} ({lang}, AI voice {voice_name})")
    return SpeakResult(
        audio_path=str(dest),
        format=format,
        duration_s=duration,
        language=lang,
        voice=voice_name,
        style=style,
        chars=len(body),
        chunks=len(chunks),
        verification=verification,
        warnings=warnings,
        usage=ledger.usage(),
    )
