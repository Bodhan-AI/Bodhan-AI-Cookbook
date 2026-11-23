"""Local media inspection and extraction. No model calls or content recognition."""

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import shutil
import subprocess
import wave


class MediaError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MediaInfo:
    source: str
    duration_seconds: float
    video_start_seconds: float
    audio_start_seconds: float
    video_stream_index: int
    audio_stream_index: int
    audio_channels: int
    audio_sample_rate: int


def tool_path(name: str, explicit: str | None = None) -> str:
    """Use installed tools only; runtime processing never downloads executables."""
    executable = shutil.which(explicit or name)
    if not executable:
        raise MediaError('missing_dependency', f'{name} is unavailable. Install ffmpeg/ffprobe or supply --{name} with its executable path.')
    return executable


def run_media(command: list[str], timeout: float) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise MediaError('media_timeout', 'Media processing timed out. Check the input or increase --media-timeout.') from exc
    except OSError as exc:
        raise MediaError('media_process_error', 'The media executable could not start. Check its path and permissions.') from exc
    if result.returncode:
        # Provider credentials are never passed to these local processes.
        detail = result.stderr.strip()[-1000:]
        raise MediaError('invalid_media', f'Media processing failed: {detail}')
    return result.stdout


def _number(value: object, default: float | None = None) -> float:
    try:
        parsed = float(value)
        if math.isfinite(parsed):
            return parsed
    except (TypeError, ValueError):
        pass
    if default is not None:
        return default
    raise MediaError('invalid_metadata', 'Media duration/timing metadata is missing or invalid.')


def inspect_video(source: Path, ffprobe: str, timeout: float = 30, allow_short: bool = False) -> MediaInfo:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise MediaError('missing_input', 'The input video does not exist or is not a regular file.')
    if source.suffix.lower() not in {'.mp4', '.mov', '.mkv', '.webm'}:
        raise MediaError('unsupported_format', 'Use an MP4, MOV, MKV, or WebM video.')
    raw = run_media([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(source)], timeout)
    try:
        metadata = json.loads(raw)
        streams = metadata['streams']
        if not isinstance(streams, list):
            raise TypeError
        videos = [s for s in streams if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
        audios = [s for s in streams if s.get('codec_type') == 'audio']
        if not videos:
            raise MediaError('missing_video', 'The input has no playable video stream.')
        if not audios:
            raise MediaError('missing_audio', 'The input has no audio stream to prepare.')
        # A documented initial policy: use the first playable video and first audio.
        video, audio = videos[0], audios[0]
        fmt = metadata.get('format', {})
        start = _number(video.get('start_time'), _number(fmt.get('start_time'), 0.0))
        fallback_duration = _number(fmt.get('duration'), -1.0) + _number(fmt.get('start_time'), 0.0) - start
        duration = _number(video.get('duration'), fallback_duration)
        audio_start = _number(audio.get('start_time'), _number(fmt.get('start_time'), 0.0))
        channels, rate = int(audio['channels']), int(audio['sample_rate'])
        video_index, audio_index = int(video['index']), int(audio['index'])
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise MediaError('invalid_metadata', 'ffprobe returned an incomplete or invalid media description.') from exc
    if duration <= 0 or channels <= 0 or rate <= 0:
        raise MediaError('invalid_metadata', 'The input has no positive duration or valid audio format.')
    if duration > 900 or (duration < 60 and not allow_short):
        raise MediaError('unsupported_duration', 'Use a 1–15 minute video. For short test fixtures only, pass --allow-short-input.')
    return MediaInfo(str(source), duration, start, audio_start, video_index, audio_index, channels, rate)


def extract_audio(info: MediaInfo, destination: Path, ffmpeg: str, timeout: float = 120) -> dict:
    """Produce 16 kHz PCM mono, aligned to the first displayed video frame.

    The original audio remains in the source video. Subtract the video origin
    from audio presentation timestamps, then insert/drop leading samples to
    begin at video time zero. Preserve gaps; pad/trim to the video duration.
    """
    if destination.exists():
        raise MediaError('output_exists', 'Audio output already exists; choose a fresh output directory.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    filters = f'asetpts=PTS-({info.video_start_seconds})/TB,aresample=16000:async=1:first_pts=0,apad'
    try:
        run_media([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-copyts', '-i', info.source,
                   '-map', f'0:{info.audio_stream_index}', '-vn', '-af', filters,
                   '-t', str(info.duration_seconds), '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(destination)], timeout)
        with wave.open(str(destination), 'rb') as audio:
            result = {'path': str(destination.resolve()), 'duration_seconds': audio.getnframes() / audio.getframerate(),
                      'sample_rate': audio.getframerate(), 'channels': audio.getnchannels(), 'sample_width_bytes': audio.getsampwidth(),
                      'timeline_origin': 'first_video_frame', 'source_audio_offset_seconds': info.audio_start_seconds - info.video_start_seconds}
        if (result['sample_rate'], result['channels'], result['sample_width_bytes']) != (16000, 1, 2):
            raise MediaError('invalid_audio', 'Extraction did not produce the expected 16 kHz mono PCM16 WAV.')
        if abs(result['duration_seconds'] - info.duration_seconds) > 0.01:
            raise MediaError('invalid_audio', 'Extracted audio duration does not match the video timeline.')
        return result
    except (wave.Error, EOFError, OSError) as exc:
        raise MediaError('invalid_audio', 'Extracted audio is unreadable or could not be written.') from exc


def describe(info: MediaInfo) -> dict:
    return asdict(info)
