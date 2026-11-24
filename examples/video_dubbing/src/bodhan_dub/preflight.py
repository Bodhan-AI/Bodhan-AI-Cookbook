"""
Preflight validator for bodhan_dub.
Performs comprehensive system, input media, language, and cost validation before processing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from bodhan_dub.config import Settings
from bodhan_dub.languages import LanguageRegistry


class PreflightError(Exception):
    """Raised when preflight checks fail, requiring exit code 2."""

    pass


class MediaInfo:
    """Parsed stream metadata from ffprobe."""

    def __init__(
        self,
        duration: float,
        has_video: bool,
        has_audio: bool,
        audio_channels: int = 2,
        audio_sample_rate: int = 44100,
        video_codec: str = "h264",
        fps: float = 30.0,
        rotation: int = 0,
    ):
        self.duration = duration
        self.has_video = has_video
        self.has_audio = has_audio
        self.audio_channels = audio_channels
        self.audio_sample_rate = audio_sample_rate
        self.video_codec = video_codec
        self.fps = fps
        self.rotation = rotation


def check_tool_installed(tool_name: str) -> str:
    """Verifies that an external executable (ffmpeg, ffprobe) is available in PATH."""
    path = shutil.which(tool_name)
    if not path:
        raise PreflightError(
            f"Required system dependency '{tool_name}' was not found in PATH. "
            f"Please install ffmpeg (including ffprobe) to run bodhan_dub."
        )
    return path


def check_disk_space(target_path: Path, min_free_mb: float = 500.0) -> None:
    """B20: Verifies sufficient free disk space in the target directory."""
    check_dir = target_path if target_path.is_dir() else target_path.parent
    if not check_dir.exists():
        check_dir = check_dir.parent
    try:
        usage = shutil.disk_usage(str(check_dir))
        free_mb = usage.free / (1024 * 1024)
        if free_mb < min_free_mb:
            raise PreflightError(
                f"Insufficient disk space in '{check_dir}': {free_mb:.1f} MB available, "
                f"requires at least {min_free_mb:.1f} MB free."
            )
    except PreflightError:
        raise
    except Exception:
        pass


def probe_media_file(input_path: Path) -> MediaInfo:
    """Runs ffprobe on the input file and extracts audio/video stream details."""
    if not input_path.exists():
        raise PreflightError(f"Input file does not exist: {input_path}")

    ffprobe = check_tool_installed("ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,channels,sample_rate,r_frame_rate,tags",
        "-of",
        "json",
        str(input_path),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(proc.stdout)
    except subprocess.CalledProcessError as e:
        raise PreflightError(f"Failed to probe media file '{input_path}': {e.stderr.strip()}")
    except Exception as e:
        raise PreflightError(f"ffprobe execution error on '{input_path}': {str(e)}")

    streams = data.get("streams", [])
    format_info = data.get("format", {})

    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    if not has_video:
        raise PreflightError(
            f"Input file '{input_path}' contains no video stream! bodhan_dub requires an MP4, MOV, MKV or WebM video."
        )

    if not has_audio:
        raise PreflightError(f"Input file '{input_path}' contains no audio stream to transcribe!")

    try:
        duration = float(format_info.get("duration", 0.0))
    except (ValueError, TypeError):
        duration = 0.0

    # Extract audio stream details
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    primary_audio = audio_streams[0]
    channels = int(primary_audio.get("channels", 2))
    sample_rate = int(primary_audio.get("sample_rate", 44100))

    # Extract video stream details
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    primary_video = video_streams[0]
    codec = primary_video.get("codec_name", "unknown")

    # Check rotation in tags
    rotation = 0
    tags = primary_video.get("tags", {})
    if "rotate" in tags:
        try:
            rotation = int(tags["rotate"])
        except ValueError:
            pass

    return MediaInfo(
        duration=duration,
        has_video=has_video,
        has_audio=has_audio,
        audio_channels=channels,
        audio_sample_rate=sample_rate,
        video_codec=codec,
        rotation=rotation,
    )


def validate_duration(duration_seconds: float) -> Tuple[bool, Optional[str]]:
    """
    Validates duration per spec: 1 to 15 minutes (60s to 900s).
    Returns (is_ok, warning_or_error_message).
    """
    if duration_seconds < 10.0:
        raise PreflightError(
            f"Input duration ({duration_seconds:.1f}s) is absurdly short (< 10s). Refusing processing."
        )
    if duration_seconds > 1800.0:
        raise PreflightError(
            f"Input duration ({duration_seconds / 60:.1f} min) is absurdly long (> 30 min). Refusing processing."
        )

    if duration_seconds < 60.0:
        return (
            True,
            f"Input video duration ({duration_seconds:.1f}s) is under the 1-minute recommendation; proceeding with warning.",
        )
    if duration_seconds > 900.0:
        return (
            True,
            f"Input video duration ({duration_seconds / 60:.1f} min) exceeds the 15-minute recommendation; proceeding with warning.",
        )

    return True, None


def estimate_pipeline_cost(
    duration_seconds: float,
    num_languages: int,
    settings: Settings,
) -> Dict[str, float]:
    """
    Computes upfront cost estimate in INR before starting paid calls.
    - Transcribe: duration_minutes * cost_asr_per_minute (shared) + duration_minutes * cost_asr_per_minute * num_languages (QA)
    - Translate: ~150 words/min = ~200 tokens/min * duration_min * num_languages * cost_translate
    - Speak: ~150 words/min = ~900 chars/min * duration_min * num_languages * cost_speak
    """
    minutes = max(0.2, duration_seconds / 60.0)

    # ASR: Shared English ASR + Indic QA back-transcription per language
    asr_shared = minutes * settings.cost_asr_per_minute
    asr_qa = minutes * settings.cost_asr_per_minute * num_languages
    asr_total = asr_shared + asr_qa

    # Translation: Forward + back-translation check
    tokens_per_min = 250
    total_tokens = tokens_per_min * minutes * num_languages * 2  # forward + back
    translate_total = (total_tokens / 10000.0) * settings.cost_translate_per_10k_tokens

    # TTS: Indic synthesis
    chars_per_min = 950
    total_chars = chars_per_min * minutes * num_languages
    tts_total = (total_chars / 10000.0) * settings.cost_tts_per_10k_chars

    total_est = asr_total + translate_total + tts_total

    return {
        "duration_minutes": round(minutes, 2),
        "languages_count": num_languages,
        "asr_estimate_inr": round(asr_total, 2),
        "translate_estimate_inr": round(translate_total, 2),
        "tts_estimate_inr": round(tts_total, 2),
        "total_estimate_inr": round(total_est, 2),
    }


def validate_voices_mapping(
    voices_arg: Optional[Union[str, Path]],
    registry: LanguageRegistry,
) -> Dict[str, str]:
    """
    C5: Validates custom voices specification against LanguageRegistry.
    Raises PreflightError if any specified language or voice is unknown.
    """
    if not voices_arg:
        return {}

    custom_voices: Dict[str, str] = {}
    v_path = Path(str(voices_arg))
    if v_path.exists() and v_path.is_file():
        import yaml
        try:
            content = yaml.safe_load(v_path.read_text(encoding="utf-8"))
            if isinstance(content, dict):
                custom_voices = {str(k).strip(): str(v).strip() for k, v in content.items()}
        except Exception as e:
            raise PreflightError(f"Failed to parse custom voices file '{v_path}': {e}")
    else:
        # Parse comma-separated "lang:voice" pairs (e.g. "hi:invalid_voice" or "hi:Kavya,ta:Deepa")
        raw_str = str(voices_arg).strip()
        for pair in raw_str.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if ":" not in pair:
                raise PreflightError(f"Invalid --voices format '{pair}'. Expected 'lang:voice' or a YAML file.")
            l_code, v_name = pair.split(":", 1)
            custom_voices[l_code.strip()] = v_name.strip()

    # Validate against registry
    supported_langs = registry.get_supported_target_codes()
    for l_code, v_name in custom_voices.items():
        if l_code not in registry.production_tier:
            raise PreflightError(
                f"Unsupported language '{l_code}' in --voices. "
                f"Supported languages: {', '.join(supported_langs)}"
            )
        allowed_voices = registry.get_voices(l_code)
        if v_name not in allowed_voices:
            raise PreflightError(
                f"Unknown voice '{v_name}' for language '{l_code}' in --voices. "
                f"Allowed voices for '{l_code}': {', '.join(allowed_voices) if allowed_voices else 'None'}"
            )

    return custom_voices


def run_preflight_checks(
    input_path: Path,
    target_langs: List[str],
    settings: Settings,
    registry: LanguageRegistry,
    estimate_only: bool = False,
    mock_mode: bool = False,
    voices_arg: Optional[Union[str, Path]] = None,
) -> Tuple[MediaInfo, Dict[str, float]]:
    """Runs all preflight checks, raising PreflightError on failure."""
    # 1. System tools
    check_tool_installed("ffmpeg")
    check_tool_installed("ffprobe")

    # B20: Disk space check (min 500 MB or 3x input video size)
    input_size_mb = (input_path.stat().st_size / (1024 * 1024)) if input_path.exists() else 0.0
    required_space_mb = max(500.0, input_size_mb * 3.0)
    check_disk_space(input_path, min_free_mb=required_space_mb)

    # 2. Input media probe
    media_info = probe_media_file(input_path)
    validate_duration(media_info.duration)

    # 3. Target languages validation
    valid_langs = registry.validate_target_langs(target_langs)

    # C5: Validate custom voices against registry
    if voices_arg:
        validate_voices_mapping(voices_arg, registry)

    # 4. API keys check (unless mock mode)
    if not mock_mode:
        missing_keys = []
        if not settings.bodhan_transcribe_key:
            missing_keys.append("BODHAN_TRANSCRIBE_KEY")
        if not settings.bodhan_translate_key:
            missing_keys.append("BODHAN_TRANSLATE_KEY")
        if not settings.bodhan_speak_key:
            missing_keys.append("BODHAN_SPEAK_KEY")

        if missing_keys and not estimate_only:
            raise PreflightError(
                f"Missing required Bodhan API keys in environment: {', '.join(missing_keys)}. "
                "Please configure them in your .env file or environment variables."
            )

        # B20: LLM configuration check
        if settings.llm_api_key and not settings.llm_base_url:
            raise PreflightError("LLM_API_KEY is configured but LLM_BASE_URL is missing or invalid.")

    # 5. Cost estimate
    estimates = estimate_pipeline_cost(media_info.duration, len(valid_langs), settings)
    return media_info, estimates
