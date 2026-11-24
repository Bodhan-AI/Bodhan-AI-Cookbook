"""transcribe_audio: speech-to-text for audio or video of any length via Indic-Transcribe (30 s per request)."""

from __future__ import annotations

import asyncio
import json

from ..catalog import STT_LANGS, normalize_language, require_language
from ..errors import BodhanToolError
from ..media import SAMPLE_RATE, decode_audio, plan_chunks, resolve_input, slice_samples, wav_bytes
from ..models import TranscribeResult
from ..text import detect_script, has_repetition
from ..usage import transcribe_cost
from .common import get_context, new_ledger, output_dir, output_path, preview, report_spend_on_error, write_text

MIN_SPEECH_S = 0.25


@report_spend_on_error
async def transcribe_audio(
    path: str,
    language: str = "auto",
    for_subtitles: bool = False,
    output_dir_: str | None = None,
    max_cost_inr: float | None = None,
) -> TranscribeResult:
    src = resolve_input(path)
    lang = normalize_language(language) or "auto"
    if lang != "auto":
        lang = require_language(lang, STT_LANGS, "speech-to-text")
    ledger = new_ledger(max_cost_inr)
    client = get_context().client

    samples = await asyncio.to_thread(decode_audio, src)
    duration = round(len(samples) / SAMPLE_RATE, 2)
    if for_subtitles:
        spans = plan_chunks(samples, target_s=5.0, min_s=1.5, max_s=12.0, min_silence_ms=150)
    else:
        spans = plan_chunks(samples, target_s=20.0, min_s=3.0, max_s=28.0)
    voiced = [(i, s) for i, s in enumerate(spans) if s.speech_s >= MIN_SPEECH_S]
    # Fail fast, before spending anything, if the whole file cannot fit the budget.
    ledger.reserve(transcribe_cost(sum(s.duration for _, s in voiced)), f"Transcribing {duration:.0f}s of audio")

    async def run(i: int, span) -> str:
        audio = wav_bytes(slice_samples(samples, span))
        return await client.transcribe(
            audio,
            seconds=span.duration,
            language=None if lang == "auto" else lang,
            ledger=ledger,
            filename=f"{src.stem}_{i:04d}.wav",
        )

    results = await asyncio.gather(*(run(i, s) for i, s in voiced), return_exceptions=True)

    texts: dict[int, str] = {}
    errors: list[BodhanToolError] = []
    warnings: list[str] = []
    for (i, span), res in zip(voiced, results, strict=True):
        if isinstance(res, BodhanToolError):
            errors.append(res)
            warnings.append(f"Segment {span.start:.1f}-{span.end:.1f}s failed: {res.code}")
        elif isinstance(res, BaseException):
            raise res
        else:
            texts[i] = res
            if has_repetition(res):
                warnings.append(f"Segment {span.start:.1f}-{span.end:.1f}s contains repeated text; check it.")
    if errors and not texts:
        raise errors[0].with_details(segments_failed=len(errors))

    segments = [
        {
            "index": i,
            "start": s.start,
            "end": s.end,
            "text": texts.get(i, ""),
            **({"error": "failed"} if i not in texts and s.speech_s >= MIN_SPEECH_S else {}),
        }
        for i, s in enumerate(spans)
        if s.speech_s >= MIN_SPEECH_S
    ]
    transcript = "\n".join(seg["text"] for seg in segments if seg["text"])
    rep = detect_script(transcript)
    if not transcript.strip():
        warnings.append("No speech was recognised in this file.")
    elif lang == "auto" and rep.unsupported_script:
        warnings.append(f"Transcript is in {rep.dominant} script, which is not a supported language.")

    out_dir = output_dir(output_dir_)
    fp = {"src": str(src), "size": src.stat().st_size, "lang": lang, "subs": for_subtitles}
    tpath = write_text(output_path(out_dir, src.stem, "transcript", "txt", fp), transcript + "\n")
    spath = write_text(
        output_path(out_dir, src.stem, "segments", "json", fp),
        json.dumps({"source": str(src), "language": lang, "segments": segments}, ensure_ascii=False, indent=1),
    )
    return TranscribeResult(
        transcript_path=str(tpath),
        segments_path=str(spath),
        preview=preview(transcript),
        char_count=len(transcript),
        duration_s=duration,
        language_requested=lang,
        detected_script=rep.dominant,
        likely_languages=rep.likely_languages,
        segments=len(segments),
        skipped_silent_segments=len(spans) - len(voiced),
        failed_segments=len(errors),
        complete=not errors,
        warnings=warnings,
        usage=ledger.usage(),
    )
