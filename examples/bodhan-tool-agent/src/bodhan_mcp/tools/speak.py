from __future__ import annotations

import io
import re
import subprocess
import tempfile
import wave
from pathlib import Path

from ..cache import ComputeResult, DiskCache, cost_speak, hash_text, make_key
from ..client import BodhanClient
from ..languages import normalize_lang_code, resolve_voice_id
from ..models import ErrorCode, ToolError, Usage, failure, success
from ..workspace import Workspace
from .inspect import audio_duration_seconds

MAX_TTS_CHARS = 500
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?।॥])(?=\s|$)")
_AUDIO_FORMATS = {"wav", "mp3", "ogg", "flac", "m4a", "aac"}
_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}


def _default_cache(workspace: Workspace, client: BodhanClient | None) -> DiskCache:
    if client is not None and type(client) is not BodhanClient:
        return DiskCache(workspace.out_dir / ".cache")
    return DiskCache.from_env()


def _ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise ToolError(
            ErrorCode.INTERNAL,
            "FFmpeg is required to process and merge audio output.",
            hint="Reinstall the project dependencies, including imageio-ffmpeg.",
        ) from None


def _tts_chunks(text: str, max_chars: int = MAX_TTS_CHARS) -> list[str]:
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_BOUNDARY.split(text):
        while len(sentence) > max_chars:
            split_at = max(
                sentence.rfind(" ", 0, max_chars + 1),
                sentence.rfind("\n", 0, max_chars + 1),
            )
            split_at = split_at + 1 if split_at > 0 else max_chars
            piece, sentence = sentence[:split_at], sentence[split_at:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece)
        if current and len(current) + len(sentence) > max_chars:
            chunks.append(current)
            current = ""
        current += sentence
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def _join_wav(chunks: list[bytes]) -> bytes | None:
    if not chunks:
        return None
    try:
        params = None
        frames: list[bytes] = []
        for content in chunks:
            with wave.open(io.BytesIO(content), "rb") as source:
                current_params = (
                    source.getnchannels(),
                    source.getsampwidth(),
                    source.getframerate(),
                    source.getcomptype(),
                    source.getcompname(),
                )
                if params is not None and current_params != params:
                    return None
                params = current_params
                frames.append(source.readframes(source.getnframes()))
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            assert params is not None
            target.setnchannels(params[0])
            target.setsampwidth(params[1])
            target.setframerate(params[2])
            target.setcomptype(params[3], params[4])
            target.writeframes(b"".join(frames))
        return output.getvalue()
    except (EOFError, RuntimeError, wave.Error):
        return None


def _convert_wav_to_mp3(wav_bytes: bytes) -> bytes:
    executable = _ffmpeg_executable()
    try:
        converted = subprocess.run(
            [
                executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-f",
                "mp3",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "4",
                "pipe:1",
            ],
            input=wav_bytes,
            capture_output=True,
            timeout=60,
            check=True,
        )
        return converted.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError(
            ErrorCode.INTERNAL,
            f"Failed to convert generated WAV audio to MP3: {exc}",
        ) from exc


def _merge_audio_ffmpeg(
    chunks: list[bytes],
    formats: list[str],
    output_format: str,
) -> bytes:
    executable = _ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="bodhan-tts-merge-") as directory:
        tmp_dir = Path(directory)
        part_paths: list[Path] = []
        for index, (audio, ext) in enumerate(zip(chunks, formats, strict=True), start=1):
            part = tmp_dir / f"part-{index:04d}.{ext}"
            part.write_bytes(audio)
            part_paths.append(part)

        out_path = tmp_dir / f"merged.{output_format}"
        cmd = [executable, "-hide_banner", "-loglevel", "error", "-y"]
        for part in part_paths:
            cmd.extend(["-i", str(part)])

        if len(part_paths) > 1:
            streams = "".join(f"[{i}:a]" for i in range(len(part_paths)))
            cmd.extend(
                [
                    "-filter_complex",
                    f"{streams}concat=n={len(part_paths)}:v=0:a=1[outa]",
                    "-map",
                    "[outa]",
                ]
            )

        if output_format == "mp3":
            cmd.extend(["-f", "mp3", "-codec:a", "libmp3lame", "-q:a", "4"])
        else:
            cmd.extend(["-f", "wav", "-codec:a", "pcm_s16le"])
        cmd.append(str(out_path))

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise ToolError(
                ErrorCode.UPSTREAM_TIMEOUT,
                "Merging synthesized audio chunks exceeded 120 seconds.",
            ) from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = ""
            if isinstance(exc, subprocess.CalledProcessError):
                detail = exc.stderr.decode("utf-8", errors="replace")[-300:]
            raise ToolError(
                ErrorCode.INTERNAL,
                f"Failed to merge synthesized audio chunks into {output_format}: {detail or exc}",
            ) from exc

        return out_path.read_bytes()


def _validate_audio(content: bytes, extension: str, index: int, total: int) -> None:
    if extension == "wav":
        try:
            with wave.open(io.BytesIO(content), "rb") as audio:
                if audio.getnframes() < 1:
                    raise wave.Error("empty audio stream")
        except (EOFError, RuntimeError, wave.Error) as exc:
            raise ToolError(
                ErrorCode.PARTIAL_FAILURE,
                f"Speech output chunk {index}/{total} is not a valid WAV file.",
                hint="Check that the configured speech endpoint returns complete WAV audio.",
            ) from exc
        return
    executable = _ffmpeg_executable()
    try:
        decoded = subprocess.run(
            [
                executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-f",
                "null",
                "-",
            ],
            input=content,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            ErrorCode.UPSTREAM_TIMEOUT,
            f"Validating speech output chunk {index}/{total} exceeded 30 seconds.",
        ) from exc
    except OSError as exc:
        raise ToolError(
            ErrorCode.INTERNAL,
            f"FFmpeg failed while validating speech output: {exc}",
        ) from exc
    if decoded.returncode != 0:
        raise ToolError(
            ErrorCode.PARTIAL_FAILURE,
            f"Speech output chunk {index}/{total} is not valid decodable audio.",
            hint="Check the configured speech endpoint content type and audio bytes.",
        )


def speak_text(
    workspace: Workspace,
    text_or_file: str,
    language: str,
    voice_id: str | None = None,
    client: BodhanClient | None = None,
    *,
    voice_gender: str | None = None,
    output_format: str | None = None,
    max_chunk_chars: int = MAX_TTS_CHARS,
    bypass_cache: bool = False,
    cache: DiskCache | None = None,
) -> dict:
    try:
        norm_lang = normalize_lang_code(language)
        if output_format is not None and output_format not in {"wav", "mp3"}:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"`output_format` must be 'wav' or 'mp3', got {output_format!r}.",
            )
        resolved_voice = resolve_voice_id(norm_lang, voice_id=voice_id, voice_gender=voice_gender)

        value = text_or_file.strip()
        source_path: Path | None = None
        if Workspace.is_probable_file_ref(value, _TEXT_SUFFIXES):
            source_path = workspace.resolve_input(value, suffixes=_TEXT_SUFFIXES)
            try:
                content = source_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ToolError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Could not decode {source_path.name!r} as UTF-8 text.",
                    hint="Save the file as UTF-8 and try again.",
                ) from exc
            source_name = source_path.stem
        else:
            content = value
            source_name = "tts_output"

        content = content.strip()
        if not content:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, "TTS input text is empty.")

        chunks = _tts_chunks(content, max_chars=max_chunk_chars)
        api_client = client or BodhanClient()
        disk_cache = cache or _default_cache(workspace, client)
        audio_chunks: list[bytes] = []
        formats: list[str] = []
        charged_cost = 0.0
        avoided_cost = 0.0
        total_latency_ms = 0
        all_cached = True

        for index, chunk in enumerate(chunks, start=1):
            cache_key = make_key(
                "speak",
                "chunk",
                {
                    "model": "indic-speak",
                    "language": norm_lang,
                    "voice_id": resolved_voice,
                },
                [hash_text(chunk)],
            )

            def _compute(chunk_text: str = chunk, idx: int = index) -> ComputeResult:
                resp = api_client.speak(
                    text=chunk_text,
                    language=norm_lang,
                    voice_id=resolved_voice,
                )
                audio = resp.get("audio_bytes")
                if not isinstance(audio, bytes) or not audio:
                    raise ToolError(
                        ErrorCode.PARTIAL_FAILURE,
                        f"Speech synthesis returned no audio for text chunk {idx}/{len(chunks)}.",
                        hint="Retry the call or use a shorter input.",
                    )
                ext = str(resp.get("format", "wav")).lower().lstrip(".")
                if ext not in _AUDIO_FORMATS:
                    raise ToolError(
                        ErrorCode.UPSTREAM_ERROR,
                        f"Speech synthesis returned unsupported audio format {ext!r}.",
                    )
                _validate_audio(audio, ext, idx, len(chunks))
                resp_usage = resp.get("usage")
                chunk_cost = (
                    resp_usage.cost_inr
                    if isinstance(resp_usage, Usage) and resp_usage.cost_inr > 0
                    else cost_speak(len(chunk_text))
                )
                latency = resp_usage.latency_ms if isinstance(resp_usage, Usage) else 0
                return ComputeResult(
                    value={"format": ext, "latency_ms": latency},
                    blobs={f"audio.{ext}": audio},
                    cost_inr=chunk_cost,
                )

            cached_res = disk_cache.get_or_compute_sync(
                "speak", cache_key, _compute, bypass=bypass_cache
            )
            ext = str(cached_res.entry.value["format"])
            blob_path = cached_res.entry.blobs[f"audio.{ext}"]
            audio_chunks.append(blob_path.read_bytes())
            formats.append(ext)
            charged_cost += cached_res.charged_inr
            avoided_cost += cached_res.avoided_inr
            total_latency_ms += (
                0 if cached_res.cached else int(cached_res.entry.value.get("latency_ms", 0))
            )
            if not cached_res.cached:
                all_cached = False

        target_format = output_format or (
            formats[0] if len(set(formats)) == 1 and formats[0] in {"wav", "mp3"} else "wav"
        )
        combined_wav = _join_wav(audio_chunks) if all(fmt == "wav" for fmt in formats) else None
        if combined_wav is not None:
            if target_format == "mp3":
                final_bytes = _convert_wav_to_mp3(combined_wav)
                _validate_audio(final_bytes, "mp3", 1, 1)
            else:
                final_bytes = combined_wav
        elif len(audio_chunks) == 1 and formats[0] == target_format:
            final_bytes = audio_chunks[0]
        else:
            final_bytes = _merge_audio_ffmpeg(audio_chunks, formats, target_format)
            _validate_audio(final_bytes, target_format, 1, 1)

        audio_artifact = workspace.write_bytes(
            source_name, f".{target_format}", final_bytes, kind="audio"
        )
        audio_paths = [audio_artifact.path]
        total_audio_duration = audio_duration_seconds(Path(audio_artifact.path))

        usage = Usage(
            model="indic-speak",
            cost_inr=round(charged_cost, 6),
            latency_ms=total_latency_ms,
            cached=all_cached,
            avoided_inr=round(avoided_cost, 6),
            units={"input_characters": float(len(content)), "chunks": float(len(chunks))},
        )
        return success(
            {
                "language": norm_lang,
                "voice_id": resolved_voice,
                "ai_generated": True,
                "character_count": len(content),
                "audio_format": target_format,
                "audio_path": audio_artifact.path,
                "audio_paths": audio_paths,
                "duration_seconds": (
                    round(total_audio_duration, 3) if total_audio_duration is not None else None
                ),
                "chunk_count": len(chunks),
                "verification": {
                    "nonempty_audio_chunks": len(audio_chunks),
                    "complete": len(audio_chunks) == len(chunks),
                },
            },
            artifacts=[audio_artifact],
            usage=usage,
        )
    except ToolError as err:
        return failure(err)
    except OSError as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"Text-to-speech could not read {text_or_file!r}: {exc.strerror or exc}",
            )
        )
    except Exception as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"Text-to-speech failed: {type(exc).__name__}: {exc}",
                details={"language": language},
            )
        )