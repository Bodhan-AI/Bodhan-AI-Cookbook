"""FFmpeg-backed ingest, conservative speech detection and sample-aligned rendering."""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
import wave
from array import array
from contextlib import ExitStack
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

ASR_RATE = 16_000
DUB_RATE = 24_000
MIX_RATE = 48_000
FRAME_SECONDS = 0.02
MAX_WINDOW_SECONDS = 30.0
MAX_TEMPO = 1.2
SPEECH_TARGET_LUFS = -16.0
MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}


class MediaError(RuntimeError):
    """An input or an FFmpeg operation could not be processed."""


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_audio: bool
    video_offset: float = 0.0
    video_index: int = 0
    video_codec: str = "h264"
    source_duration: float | None = None


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    wav: Path
    speech: tuple[tuple[float, float], ...]
    forced_overlap: bool = False


@dataclass(frozen=True)
class FittedClip:
    path: Path | None
    duration: float
    raw_duration: float
    tempo: float
    reason: str | None = None
    trim_start: float = 0.0
    trim_end: float = 0.0
    loudness_lufs: float | None = None
    gain_db: float = 0.0


@dataclass(frozen=True)
class Placement:
    path: Path
    start: float
    duration: float


def _call(*args: str, timeout: int = 240) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            args, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MediaError(f"{args[0]} failed: {error}") from error
    if result.returncode:
        details = result.stderr.decode(errors="replace").strip().splitlines()
        raise MediaError(f"{args[0]} failed: {' '.join(details[-3:])[:1000]}")
    return result


def _ffmpeg(*args: str, loglevel: str = "error") -> subprocess.CompletedProcess:
    return _call(
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", loglevel, "-y", *args
    )


def _probe(path: Path) -> dict:
    result = _call(
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
        str(path.resolve()),
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise MediaError("FFprobe returned invalid metadata") from error


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _metadata_duration(metadata: dict, video: dict, offset: float) -> float | None:
    duration = _number(video.get("duration"))
    if duration is not None and duration > 0:
        return duration
    # Matroska commonly carries the video track's end timestamp as a tag.
    tag = video.get("tags", {}).get("DURATION")
    if tag:
        try:
            hours, minutes, seconds = map(float, tag.split(":"))
            duration = hours * 3600 + minutes * 60 + seconds - offset
            if math.isfinite(duration) and duration > 0:
                return duration
        except ValueError:
            pass
    return _number(metadata.get("format", {}).get("duration"))


def probe_input(path: Path, *, excerpt_seconds: float | None = None) -> MediaInfo:
    """Keep the 60–900 second input contract, then optionally process a prefix."""
    path = Path(path)
    if path.suffix.lower() not in MEDIA_SUFFIXES or not path.is_file():
        raise MediaError("Input must be a readable MP4, MOV, MKV or WebM file")
    metadata = _probe(path)
    streams = metadata.get("streams", [])
    video = next((
        stream for stream in streams
        if stream.get("codec_type") == "video"
        and not stream.get("disposition", {}).get("attached_pic", 0)
    ), None)
    if video is None:
        raise MediaError("Input has no video stream")
    offset = _number(video.get("start_time")) or 0.0
    full_duration = _metadata_duration(metadata, video, offset)
    if full_duration is None or not 60 <= full_duration <= 900:
        raise MediaError(f"Input video must be 60–900 seconds long, got {full_duration}")
    duration = full_duration
    if excerpt_seconds is not None:
        if not math.isfinite(excerpt_seconds) or not 1 <= excerpt_seconds <= full_duration:
            raise MediaError("Excerpt must be between one second and the video duration")
        duration = excerpt_seconds
    return MediaInfo(
        duration, any(stream.get("codec_type") == "audio" for stream in streams),
        offset, video["index"], video.get("codec_name", ""), full_duration,
    )


def _audio_wav(
    source: Path, destination: Path, duration: float, rate: int, channels: int, offset: float
) -> None:
    filters = (
        f"atrim=start={offset:.9f},asetpts=PTS-({offset:.9f})/TB,"
        f"aresample={rate}:async=1:first_pts=0,apad,atrim=duration={duration:.9f}"
    )
    _ffmpeg(
        "-copyts", "-i", str(source.resolve()), "-map", "0:a:0", "-af", filters,
        "-ac", str(channels), "-c:a", "pcm_s16le", str(destination),
    )


def _zeros(output: wave.Wave_write, frames: int, channels: int = 1) -> None:
    while frames:
        count = min(frames, 65_536)
        output.writeframesraw(bytes(count * channels * 2))
        frames -= count


def _wav_output(path: Path, rate: int, channels: int = 1) -> wave.Wave_write:
    output = wave.open(str(path), "wb")
    output.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
    return output


def extract_audio(
    path: Path, work_dir: Path, duration: float, *,
    video_offset: float = 0.0, has_audio: bool | None = None,
) -> Path:
    """Extract timeline-aligned mono PCM, including silence for delayed audio."""
    path, work_dir = Path(path), Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    destination = work_dir / "source.wav"
    if has_audio is None:
        has_audio = any(
            stream.get("codec_type") == "audio" for stream in _probe(path).get("streams", [])
        )
    if has_audio:
        _audio_wav(path, destination, duration, ASR_RATE, 1, video_offset)
    else:
        with _wav_output(destination, ASR_RATE) as output:
            _zeros(output, round(duration * ASR_RATE))
    return destination


def _pcm_samples(data: bytes) -> array:
    samples = array("h", data)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def _pcm_bytes(samples: array) -> bytes:
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def _rms_frames(wav: Path) -> tuple[float, list[float]]:
    with wave.open(str(wav), "rb") as audio:
        rate = audio.getframerate()
        frame_samples = round(rate * FRAME_SECONDS)
        levels = []
        while data := audio.readframes(frame_samples):
            samples = _pcm_samples(data)
            levels.append(math.sqrt(sum(sample * sample for sample in samples) / len(samples)))
        return audio.getnframes() / rate, levels


def speech_intervals(wav: Path) -> list[tuple[float, float]]:
    """Detect dynamic sound in the speech band and give ASR the final word."""
    analysis = Path(wav).with_name("vad.pcm.wav")
    _ffmpeg(
        "-i", str(wav), "-af", "highpass=f=105,lowpass=f=4200",
        "-ac", "1", "-c:a", "pcm_s16le", str(analysis),
    )
    duration, levels = _rms_frames(analysis)
    if not levels:
        return []
    ordered = sorted(levels)
    floor = ordered[int((len(ordered) - 1) * 0.18)]
    peak = ordered[int((len(ordered) - 1) * 0.97)]
    threshold = max(68.0, min(floor * 2.2, peak * 0.42))
    # Suppress near-stationary tones and continuous noise. Speech usually has
    # envelope changes within an audible region. Compare robust local levels
    # so a tone entering or leaving silence does not count as speech itself.
    active = []
    for index, value in enumerate(levels):
        neighbors = sorted(
            level for level in levels[max(0, index - 5):index + 6] if level >= threshold
        ) if value >= threshold else []
        last = len(neighbors) - 1
        active.append(
            len(neighbors) >= 5 and neighbors[math.ceil(last * 0.75)] >= neighbors[last // 4] * 1.20 + 18
        )

    runs: list[tuple[int, int]] = []
    beginning = None
    for index, on in enumerate([*active, False]):
        if on:
            if beginning is None:
                beginning = index
        elif beginning is not None:
            if runs and beginning - runs[-1][1] <= 12:
                runs[-1] = (runs[-1][0], index)
            else:
                runs.append((beginning, index))
            beginning = None
    return [
        (round(max(0.0, start * FRAME_SECONDS - 0.08), 6),
         round(min(duration, end * FRAME_SECONDS + 0.08), 6))
        for start, end in runs
        if end - start >= 6
    ]


def source_windows(wav: Path, directory: Path) -> list[Window]:
    """Create at most 30 seconds of ASR audio per context window."""
    intervals = speech_intervals(wav)
    groups: list[list[tuple[float, float]]] = []
    for interval in intervals:
        if not groups or interval[0] - groups[-1][-1][1] >= 1.0:
            groups.append([])
        groups[-1].append(interval)

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    windows = []
    with wave.open(str(wav), "rb") as source:
        rate = source.getframerate()
        for group in groups:
            start = group[0][0]
            final = group[-1][1]
            has_overlap = False
            while start < final:
                end = min(start + MAX_WINDOW_SECONDS, final)
                forced = False
                if end < final:
                    pauses = [
                        (previous[1], following[0])
                        for previous, following in pairwise(group)
                        if end - 8 <= previous[1] <= end and following[0] - previous[1] >= 0.12
                    ]
                    if pauses:
                        end, next_start = pauses[-1]
                    else:
                        forced = True
                        next_start = end - 0.24
                regions = tuple(
                    (max(start, region_start), min(end, region_end))
                    for region_start, region_end in group
                    if region_end > start and region_start < end
                )
                first = round(start * rate)
                exclusive_last = min(source.getnframes(), round(end * rate))
                path = directory / f"window-{len(windows):04d}.wav"
                source.setpos(first)
                with _wav_output(path, rate) as output:
                    output.writeframes(source.readframes(exclusive_last - first))
                windows.append(Window(first / rate, exclusive_last / rate, path, regions, has_overlap))
                if end >= final:
                    break
                start, has_overlap = next_start, forced
    return windows


def _loudness(path: Path) -> tuple[float | None, float | None]:
    result = _ffmpeg(
        "-i", str(path), "-af", "ebur128=peak=true:framelog=verbose",
        "-f", "null", "-", loglevel="info",
    )
    log = result.stderr.decode(errors="replace").rsplit("Summary:", 1)[-1]
    integrated = re.search(r"\bI:\s+(-?[\d.]+) LUFS", log)
    peaks = re.search(r"\bPeak:\s+(-?[\d.]+) dBFS", log)
    value = float(integrated.group(1)) if integrated else None
    peak = float(peaks.group(1)) if peaks else None
    return (value if value is not None and value > -69.9 else None), peak


def _trim_silence(path: Path) -> tuple[float, float, float, bool]:
    duration, levels = _rms_frames(path)
    if not levels or max(levels) < 16.0:
        return duration, 0.0, 0.0, False
    # Keep quiet consonants and natural within-utterance pauses.
    threshold = max(7.0, min(30.0, max(levels) * 0.0015))
    audible = [index for index, level in enumerate(levels) if level >= threshold]
    if not audible:
        return duration, 0.0, 0.0, False
    start = max(0.0, audible[0] * FRAME_SECONDS - 0.04)
    end = min(duration, (audible[-1] + 1) * FRAME_SECONDS + 0.06)
    return duration, start, max(0.0, duration - end), True


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as media:
        return media.getnframes() / media.getframerate()


def _trim_and_stretch(
    decoded: Path, adjusted: Path, trim_start: float, trim_stop: float, tempo: float,
) -> float:
    filters = f"atrim=start={trim_start:.9f}:end={trim_stop:.9f},asetpts=PTS-STARTPTS"
    if tempo != 1.0:
        filters += f",atempo={tempo:.8f}"
    _ffmpeg("-i", str(decoded), "-af", filters, "-c:a", "pcm_s16le", str(adjusted))
    return _wav_duration(adjusted)


def fit_clip(audio: bytes, directory: Path, name: str, slot_seconds: float) -> FittedClip:
    """Decode by signature and return the exact faded, normalized WAV for QA."""
    if not audio:
        return FittedClip(None, 0.0, 0.0, 1.0, "tts_empty_payload")
    if not math.isfinite(slot_seconds) or slot_seconds <= 0:
        return FittedClip(None, 0.0, 0.0, 1.0, "no_placement_room")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory, prefix="fit-") as temp:
        work = Path(temp)
        payload, decoded, adjusted = (work / item for item in ("audio", "decoded.wav", "tempo.wav"))
        payload.write_bytes(audio)
        try:
            _ffmpeg(
                "-i", str(payload), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(DUB_RATE),
                "-t", "120", "-c:a", "pcm_s16le", str(decoded),
            )
        except MediaError as error:
            return FittedClip(None, 0.0, 0.0, 1.0, f"tts_decode_failed: {error}")
        raw, trim_start, trim_end, audible = _trim_silence(decoded)
        if raw >= 119.9:
            return FittedClip(None, raw, raw, 1.0, "tts_duration_exceeds_decode_limit")
        if not audible:
            return FittedClip(None, raw, raw, 1.0, "tts_silence")
        trimmed = raw - trim_start - trim_end
        tempo = 1.0 if trimmed <= slot_seconds else min(
            MAX_TEMPO, (trimmed + 0.012) / slot_seconds
        )
        if trimmed / MAX_TEMPO > slot_seconds + 0.04:
            return FittedClip(
                None, trimmed, raw, tempo, "tts_cannot_fit_within_tempo_limit", trim_start, trim_end
            )
        duration = _trim_and_stretch(decoded, adjusted, trim_start, raw - trim_end, tempo)
        if duration > slot_seconds and tempo < MAX_TEMPO:
            tempo = min(MAX_TEMPO, tempo * (duration + 0.012) / slot_seconds)
            duration = _trim_and_stretch(decoded, adjusted, trim_start, raw - trim_end, tempo)
        if duration > slot_seconds:
            return FittedClip(
                None, duration, raw, tempo, "tts_cannot_fit_within_tempo_limit", trim_start, trim_end
            )
        if duration <= 0:
            return FittedClip(None, duration, raw, tempo, "tts_empty_after_fitting", trim_start, trim_end)

        loudness, peak = _loudness(adjusted)
        gain = 0.0 if loudness is None else SPEECH_TARGET_LUFS - loudness
        gain = max(-36.0, min(30.0, gain))
        fade = min(0.018, duration / 4)
        final = (directory / f"{name}.wav").resolve()
        for pass_index in range(2):
            filters = [f"volume={gain:.4f}dB"]
            if peak is not None and peak + gain > -1.5:
                filters.append("alimiter=limit=0.8414:attack=5:release=45:level=0:latency=1")
            filters.extend((
                f"afade=t=in:d={fade:.8f}", f"afade=t=out:st={duration-fade:.8f}:d={fade:.8f}",
            ))
            _ffmpeg("-i", str(adjusted), "-af", ",".join(filters), "-c:a", "pcm_s16le", str(final))
            placed_loudness, _ = _loudness(final)
            if pass_index == 1 or placed_loudness is None or abs(SPEECH_TARGET_LUFS - placed_loudness) <= 0.8:
                break
            corrected_gain = max(-36.0, min(30.0, gain + SPEECH_TARGET_LUFS - placed_loudness))
            if corrected_gain == gain:
                break
            gain = corrected_gain
        final_duration = _wav_duration(final)
        if final_duration > slot_seconds:
            return FittedClip(
                None, final_duration, raw, tempo, "tts_cannot_fit_within_tempo_limit", trim_start, trim_end
            )
        return FittedClip(
            final, final_duration, raw, tempo, None, trim_start, trim_end, placed_loudness, gain,
        )


def quiet_tail(wav: Path, start: float, limit: float) -> float:
    """Borrow contiguous quiet frames until an audible cue or the requested limit."""
    if limit <= start:
        return start
    with wave.open(str(wav), "rb") as source:
        rate = source.getframerate()
        first = min(round(start * rate), source.getnframes())
        stop = min(round(limit * rate), source.getnframes())
        source.setpos(first)
        while first < stop:
            count = min(round(rate * FRAME_SECONDS), stop - first)
            samples = _pcm_samples(source.readframes(count))
            rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
            if rms > 180.0 or max(map(abs, samples)) > 1000:
                break
            first += count
        return max(start, first / rate)


def _voice_bus(clips: list[Placement], duration: float, path: Path) -> None:
    frames = round(duration * DUB_RATE)
    cursor = 0
    with _wav_output(path, DUB_RATE) as output:
        for clip in sorted(clips, key=lambda item: item.start):
            first = round(clip.start * DUB_RATE)
            with wave.open(str(clip.path), "rb") as source:
                if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (DUB_RATE, 1, 2):
                    raise MediaError("Placed clips must be mono 24 kHz PCM16 WAV")
                count = source.getnframes()
                if first < cursor:
                    raise MediaError("Dub placements overlap on the absolute timeline")
                if first + count > frames or abs(count / DUB_RATE - clip.duration) > 1 / DUB_RATE:
                    raise MediaError("Dub placement duration does not match the available timeline")
                _zeros(output, first - cursor)
                while data := source.readframes(65_536):
                    output.writeframesraw(data)
            cursor = first + count
        _zeros(output, frames - cursor)


def _duck_ranges(
    clips: list[Placement], speech: list[tuple[float, float]] | None, duration: float
) -> list[tuple[int, int]]:
    regions = [(clip.start, clip.start + clip.duration) for clip in clips]
    regions.extend(speech or ())
    ranges: list[tuple[int, int]] = []
    for start, end in sorted(regions):
        first, stop = round(max(0.0, start) * MIX_RATE), round(min(duration, end) * MIX_RATE)
        if stop <= first:
            continue
        if ranges and first <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(stop, ranges[-1][1]))
        else:
            ranges.append((first, stop))
    return ranges


def _write_background(
    original: wave.Wave_read | None, stem: wave.Wave_read | None, output: wave.Wave_write,
    ranges: list[tuple[int, int]], frames: int,
) -> None:
    def read(source: wave.Wave_read | None, count: int) -> bytes:
        if source is None:
            return bytes(count * 4)
        data = source.readframes(count)
        return data.ljust(count * 4, b"\0")

    def copy_untouched(first: int, last: int) -> None:
        if original is not None:
            original.setpos(min(first, original.getnframes()))
        while first < last:
            count = min(65_536, last - first)
            output.writeframesraw(read(original, count))
            first += count

    cursor = 0
    duck_gain = 10 ** (-36 / 20)
    for first, stop in ranges:
        copy_untouched(cursor, first)
        if stem is not None:
            stem.setpos(min(first, stem.getnframes()))
        fade = min(round(0.035 * MIX_RATE), max(1, (stop - first) // 2))
        cursor = first
        while cursor < stop:
            count = min(65_536, stop - cursor)
            original_samples = _pcm_samples(read(original, count))
            stem_samples = _pcm_samples(read(stem, count)) if stem is not None else None
            for frame in range(count):
                index = cursor + frame
                fraction = min(1.0, (index - first) / fade, (stop - index - 1) / fade)
                source_gain = 1 - fraction if stem_samples is not None else 1 - fraction * (1 - duck_gain)
                for channel in range(2):
                    sample = frame * 2 + channel
                    value = original_samples[sample] * source_gain
                    if stem_samples is not None:
                        value += stem_samples[sample] * fraction
                    original_samples[sample] = round(value)
            output.writeframesraw(_pcm_bytes(original_samples))
            cursor += count
    copy_untouched(cursor, frames)


def _mux(
    source: Path, info: MediaInfo, bed: Path, speech: Path, destination: Path, *, copy: bool
) -> None:
    encoding = ["-c:v", "copy"] if copy else [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-fps_mode:v", "passthrough",
    ]
    _ffmpeg(
        "-copyts", "-itsoffset", f"{-info.video_offset:.9f}", "-i", str(source.resolve()),
        "-i", str(bed), "-i", str(speech),
        "-filter_complex", (
            "[2:a]aresample=48000,pan=stereo|c0=0.707107*c0|c1=0.707107*c0[dub];"
            "[1:a][dub]amix=inputs=2:duration=first:normalize=0,"
            "alimiter=limit=0.97:level=false:latency=true[mixed]"
        ),
        "-map", f"0:{info.video_index}", "-map", "[mixed]", *encoding,
        "-c:a", "aac", "-b:a", "192k", "-t", f"{info.duration:.9f}",
        "-map_chapters", "-1", "-metadata", "comment=AI-dubbed audio", "-movflags", "+faststart",
        str(destination),
    )


def _output_durations(path: Path) -> tuple[float, float]:
    metadata = _probe(path)
    overall = _number(metadata.get("format", {}).get("duration"))
    video = next(
        (stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "video"), None
    )
    has_audio = any(
        stream.get("codec_type") == "audio" for stream in metadata.get("streams", [])
    )
    if overall is None or video is None or not has_audio:
        raise MediaError("Rendered file lacks measurable video or an audio stream")
    video_duration = _metadata_duration(metadata, video, 0.0)
    if video_duration is None:
        raise MediaError("Rendered video duration is unavailable")
    return overall, video_duration


def render_video(
    source: Path, info: MediaInfo, clips: list[Placement], output: Path, *,
    background_stem: Path | None = None,
    speech_intervals: list[tuple[float, float]] | None = None,
) -> dict:
    """Preserve the source between dubbed regions and atomically publish a verified MP4."""
    source, output = Path(source), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".render-") as local:
        work = Path(local).resolve()
        voice, bed, candidate = (work / item for item in ("speech.wav", "bed.wav", "video.mp4"))
        _voice_bus(clips, info.duration, voice)
        loudness = _loudness(voice)[0] if clips else None
        ranges = _duck_ranges(clips, speech_intervals, info.duration)

        with ExitStack() as stack:
            original, stem = None, None
            if info.has_audio:
                original_path = work / "original.wav"
                _audio_wav(source, original_path, info.duration, MIX_RATE, 2, info.video_offset)
                original = stack.enter_context(wave.open(str(original_path), "rb"))
            output_audio = stack.enter_context(_wav_output(bed, MIX_RATE, 2))
            if background_stem is not None and ranges:
                stem_path = work / "background.wav"
                _audio_wav(Path(background_stem), stem_path, info.duration, MIX_RATE, 2, 0.0)
                stem = stack.enter_context(wave.open(str(stem_path), "rb"))
            _write_background(original, stem, output_audio, ranges, round(info.duration * MIX_RATE))

        if info.video_codec in {"h264", "hevc", "av1", "mpeg4", "vp9"}:
            try:
                _mux(source, info, bed, voice, candidate, copy=True)
                overall, video_duration = _output_durations(candidate)
                copied = max(abs(overall - info.duration), abs(video_duration - info.duration)) <= 0.10
            except MediaError:
                copied = False
        else:
            copied = False
        if not copied:
            _mux(source, info, bed, voice, candidate, copy=False)
            overall, video_duration = _output_durations(candidate)
        candidate.replace(output)

    if not ranges:
        strategy = "original_audio" if info.has_audio else "silence"
    elif background_stem is not None:
        strategy = "clean_stem_under_dub_original_elsewhere"
    else:
        strategy = "original_mix_attenuated_36_db_under_dub"
    duration_error = max(abs(overall - info.duration), abs(video_duration - info.duration))
    return {
        "duration": overall,
        "video_duration": video_duration,
        "source_duration": info.duration,
        "duration_error_seconds": duration_error,
        "duration_within_target": duration_error <= 0.1000001,
        "speech_lufs": loudness,
        "speech_loudness_within_target": abs(loudness - SPEECH_TARGET_LUFS) <= 1 if loudness is not None else None,
        "speech_loudness_measurement": "EBU R128 integrated on the placed speech bus before AAC mux",
        "audio_strategy": strategy,
        "background_loss_possible": bool(ranges and info.has_audio and background_stem is None),
        "video_copied": copied,
    }
