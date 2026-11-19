"""transcribe_audio: any audio/video file in, one transcript with timestamps out."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..core import api, cache, config, costs, media
from ..core.errors import BodhanError
from ..core.langid import identify
from ..core.schemas import Chunk, Transcript


def transcribe_audio(
    audio_path: str,
    language: str | None = None,
    out_dir: str | None = None,
    max_cost_inr: float | None = None,
    force: bool = False,
) -> Transcript:
    """Transcribe speech to a text file. Accepts WAV/FLAC/OGG/MP3, the containers the API refuses (M4A, AAC, WebM, AMR, converted locally), and any video file with an audio track; everything is normalised to 16 kHz mono WAV first. Long recordings are cut at natural pauses into windows of at most 30 seconds, transcribed, and merged, so any length works in one call. Returns the transcript path and per-window timestamps for subtitle timing. The API returns text only, so the language is identified locally from the transcript's writing system. Pass `language` only when you already know it; omitting it lets the model auto-detect."""
    src = Path(audio_path)
    if not src.is_file():
        raise BodhanError("MISSING_FILE", f"{audio_path} does not exist.", hint="Pass a path to an existing file.")
    out = Path(out_dir) if out_dir else config.default_out_dir()
    out.mkdir(parents=True, exist_ok=True)
    budget = costs.CallBudget(max_cost_inr)

    with tempfile.TemporaryDirectory(prefix="bodhan-asr-") as tmp:
        wav = Path(tmp) / "audio.wav"
        try:
            media.to_wav(str(src), str(wav))
        except BodhanError:
            raise
        except Exception as exc:
            raise BodhanError("UNSUPPORTED_FORMAT", f"{src.name} could not be decoded by ffmpeg ({type(exc).__name__}).", hint="Supported: WAV, FLAC, OGG, MP3, M4A, AAC, WebM, AMR, and video with an audio track.") from None
        duration = media.wav_duration(str(wav))
        if duration < 0.3:
            raise BodhanError("AUDIO_TOO_SHORT", f"{src.name} is {duration:.2f}s; there is nothing to transcribe.")
        windows = media.silence_cut_points(str(wav), config.ASR_TARGET_WINDOW_S, config.ASR_MAX_WINDOW_S)
        estimate = costs.asr_cost(duration)
        budget.reserve(estimate, f"Transcribing {src.name}")
        parts: list[str] = []
        chunks: list[Chunk] = []
        warnings: list[str] = []
        hits = 0
        paid_s = 0.0
        for i, (start, end, cut) in enumerate(windows):
            blob = media.wav_slice(str(wav), start, end)
            key = cache.digest("asr", language or "auto", blob)
            text = None if force else cache.get_json(key)
            if isinstance(text, dict):
                text = text.get("text")
            if isinstance(text, str):
                hits += 1
            else:
                try:
                    text = api.transcribe(blob, language, filename=f"window{i}.wav")
                except BodhanError as exc:
                    warnings.append(f"partial_failure:window_{i}:{exc.code}")
                    text = ""
                    continue
                paid_s += end - start
                if not force:
                    cache.put_json(key, {"text": text})
            parts.append(text)
            chunks.append(Chunk(index=i, start_s=round(start, 3), end_s=round(end, 3), chars=len(text), cut_at=cut))
        transcript = " ".join(p for p in parts if p).strip()
        if not transcript:
            raise BodhanError("NO_SPEECH", f"No speech was recognised in {src.name}.", hint="Check the audio is not silent or in an unsupported language.")
        for c in chunks:
            if c.cut_at == "hard":
                warnings.append(f"hard_cut_{c.index}")
        out_path = out / f"{src.stem}.transcript.txt"
        out_path.write_text(transcript, encoding="utf-8")
        guess = identify(str(out_path))
        spent = costs.asr_cost(paid_s) if paid_s else 0.0
        budget.add(spent)
        return Transcript(
            text_path=str(out_path.resolve()),
            language_hint=guess["language"],
            duration_s=round(duration, 3),
            chunks=chunks,
            cost_inr=round(spent, 4),
            cache_hits=hits,
            warnings=warnings,
        )
