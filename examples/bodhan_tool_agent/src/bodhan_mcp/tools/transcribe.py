"""transcribe_audio: any-length audio or video -> transcript + timed segments via Indic-Transcribe.

Indic-Transcribe takes at most 30 s per request and returns text only (no timestamps, no detected
language). So: decode to 16 kHz mono WAV, cut at pauses into windows of at most 29.5 s (or about 7 s
for subtitles), skip silent windows, transcribe the windows in parallel, and derive timestamps from
our own cut points. Billing is per audio second, so smaller windows cost the same.
"""

from __future__ import annotations

import asyncio
import json

from bodhan_core import audio, chunking, langs, workspace
from bodhan_core.errors import ErrorCode, ToolError
from bodhan_core.ledger import cost_transcribe
from bodhan_mcp.runtime import get_client

INLINE_TEXT_LIMIT = 1500  # short outputs come back inline, so callers need no read_text round trip

STYLES = {
    "transcript": {"max_s": audio.MAX_WINDOW_S, "min_s": None},
    "subtitle": {"max_s": 7.0, "min_s": 1.5},
}


async def transcribe_audio(
    path: str,
    language: str | None = None,
    segment_style: str = "transcript",
    max_minutes: float = 60.0,
    out_dir: str | None = None,
) -> dict:
    p = workspace.resolve_input(path)
    kind = workspace.media_kind(p)
    if kind not in {"audio", "video"}:
        raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, f"transcribe_audio needs an audio or video file, got {kind}.",
                        hint="Use inspect_media to see which tool fits this file.")
    if segment_style not in STYLES:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"segment_style must be one of {sorted(STYLES)}.")
    lang = langs.require_supported("indic-transcribe", language) if language else None

    info = await asyncio.to_thread(audio.probe, p)
    if not info["has_audio"]:
        raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, f"{p.name} has no audio stream.", hint="Nothing to transcribe.")
    if info["duration_s"] > max_minutes * 60:
        raise ToolError(ErrorCode.INVALID_ARGUMENT,
                        f"Audio is {info['duration_s'] / 60:.1f} min, over max_minutes={max_minutes} "
                        f"(≈₹{cost_transcribe(info['duration_s']):.2f}).",
                        hint="Raise max_minutes deliberately, or trim the audio.")

    out = workspace.output_dir(out_dir)
    work = out / ".work"
    wav = await asyncio.to_thread(audio.to_wav16k, p, workspace.artifact_path(work, p, ".16k.wav"))
    samples, rate = audio.read_pcm(wav)
    energies = audio.frame_energies(samples, rate)
    windows = audio.plan_windows(energies, **STYLES[segment_style])
    speech = [w for w in windows if not w.silent]

    client = get_client()
    results = await asyncio.gather(*(
        client.transcribe(audio.slice_wav(samples, rate, w.start_s, w.end_s), filename=f"{p.stem}_{i:04d}.wav",
                          duration_s=w.duration_s, language=lang)
        for i, w in enumerate(speech)
    ), return_exceptions=True)

    segments, failed, cost, cached = [], [], 0.0, 0
    previous_text = ""
    for w, r in zip(speech, results, strict=True):
        if isinstance(r, ToolError):
            failed.append({"start_s": w.start_s, "end_s": w.end_s, "error": r.info.model_dump(mode="json")})
            previous_text = ""
            continue
        if isinstance(r, BaseException):
            raise r
        cost += r.cost_inr
        cached += r.cached
        text = ((r.data or {}).get("text") or "").strip()
        if w.hard_cut_before and previous_text:
            text = chunking.stitch_overlap(previous_text, text)
        previous_text = text
        if text:
            segments.append({"id": len(segments) + 1, "start": w.start_s, "end": w.end_s, "text": text})

    if speech and not segments and failed:
        first = failed[0]["error"]
        raise ToolError(ErrorCode(first["code"]), f"Transcription failed for every window: {first['message']}",
                        hint=first.get("hint"))

    transcript = " ".join(s["text"] for s in segments)
    script = langs.detect_script(transcript)
    detected = lang or (script["candidates"][0] if len(script["candidates"]) == 1 else None)
    key = f"{lang}|{segment_style}"
    transcript_path = workspace.artifact_path(out, p, ".transcript.txt", key=key)
    segments_path = workspace.artifact_path(out, p, ".segments.json", key=key)
    transcript_path.write_text(transcript + "\n", encoding="utf-8")
    segments_path.write_text(json.dumps(segments, ensure_ascii=False, indent=1), encoding="utf-8")

    warnings = []
    if not transcript:
        warnings.append("No speech was recognised.")
    if not lang and script["ambiguous"] and script["candidates"]:
        warnings.append(f"{script['script']} script fits several languages {script['candidates']}; "
                        "pass `language` if you know it.")
    return {
        "ok": True,
        "transcript_path": str(transcript_path),
        "segments_path": str(segments_path),
        "duration_s": info["duration_s"],
        "language": detected,
        "language_source": "given" if lang else ("script" if detected else "unknown"),
        "script": script,
        "segments": len(segments),
        "windows": {"total": len(windows), "transcribed": len(speech) - len(failed),
                    "silent_skipped": len(windows) - len(speech)},
        "failed_windows": failed,
        "complete": not failed,
        "chars": len(transcript),
        "preview": workspace.preview(transcript),
        **({"text": transcript} if len(transcript) <= INLINE_TEXT_LIMIT else {}),
        "untrusted_content": True,
        "warnings": warnings,
        "cost_inr": round(cost, 4),
        "cached_windows": cached,
    }
