import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

class VideoValidationError(Exception):
    """Raised when an input video fails integrity or specification checks."""

class VideoEngine:
    """Handles video validation, FFprobe inspection, audio extraction, and video remuxing."""

    SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.ffmpeg_path = shutil.which(ffmpeg_path) or "ffmpeg"
        self.ffprobe_path = shutil.which(ffprobe_path) or "ffprobe"

    def probe(self, video_path: Path) -> dict[str, Any]:
        """Runs ffprobe on the input file and returns structured stream metadata."""
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        cmd = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(video_path)
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return json.loads(res.stdout)
        except subprocess.CalledProcessError as e:
            raise VideoValidationError(f"FFprobe failed to inspect {video_path}: {e.stderr}") from e
        except Exception as e:
            raise VideoValidationError(f"Failed to parse video metadata: {e}") from e

    def validate_video(self, video_path: Path, min_duration: float = 60.0, max_duration: float = 900.0) -> dict[str, Any]:
        """Validates video format, duration (1-15 min), streams, and integrity."""
        ext = video_path.suffix.lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise VideoValidationError(
                f"Unsupported video format: '{ext}'. Supported formats: {', '.join(sorted(self.SUPPORTED_EXTENSIONS))}"
            )

        metadata = self.probe(video_path)
        format_info = metadata.get("format", {})
        streams = metadata.get("streams", [])

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not video_stream:
            raise VideoValidationError(f"File {video_path.name} does not contain any video stream.")

        duration = float(format_info.get("duration", 0.0) or video_stream.get("duration", 0.0))
        if duration <= 0:
            raise VideoValidationError(f"Could not determine valid duration for {video_path.name}")

        # Note: If duration is outside standard range (1-15 min), log warning or handle gracefully
        if duration < min_duration:
            logger.warning("Video duration %.1fs is below recommended minimum 60s (1 min). Continuing with processing.", duration)
        elif duration > max_duration:
            logger.warning("Video duration %.1fs exceeds recommended maximum 900s (15 min). Continuing with processing.", duration)

        fps_str = video_stream.get("r_frame_rate", "30/1")
        try:
            num, den = fps_str.split("/")
            fps = float(num) / float(den) if float(den) > 0 else 30.0
        except Exception:
            fps = 30.0

        sample_rate = int(audio_stream.get("sample_rate", 16000)) if audio_stream else None
        channels = int(audio_stream.get("channels", 1)) if audio_stream else 0

        return {
            "duration": duration,
            "has_audio": audio_stream is not None,
            "fps": round(fps, 2),
            "video_codec": video_stream.get("codec_name", "unknown"),
            "audio_codec": audio_stream.get("codec_name", "none") if audio_stream else None,
            "sample_rate": sample_rate,
            "channels": channels,
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
            "raw_metadata": metadata
        }

    def extract_audio(
        self,
        video_path: Path,
        speech_audio_path: Path,
        hq_audio_path: Path | None = None,
        has_audio: bool = True,
        duration: float = 1.0
    ) -> tuple[Path, Path | None]:
        """Extracts audio from video:
        1. speech_audio_path: 16kHz mono 16-bit PCM WAV (ideal for Bodhan transcription).
        2. hq_audio_path: 48kHz stereo WAV (for background audio preservation and mixing).
        Handles videos without audio tracks gracefully by generating silence.
        """
        speech_audio_path.parent.mkdir(parents=True, exist_ok=True)

        if not has_audio:
            logger.info("Video %s contains no audio stream. Generating silent audio baseline.", video_path.name)
            import wave
            num_frames = int(duration * 16000)
            with wave.open(str(speech_audio_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x00\x00" * num_frames)

            if hq_audio_path:
                hq_audio_path.parent.mkdir(parents=True, exist_ok=True)
                num_frames_hq = int(duration * 48000)
                with wave.open(str(hq_audio_path), "wb") as wf:
                    wf.setnchannels(2)
                    wf.setsampwidth(2)
                    wf.setframerate(48000)
                    wf.writeframes(b"\x00\x00\x00\x00" * num_frames_hq)

            return speech_audio_path, hq_audio_path

        # 1. Speech track (16kHz mono)
        cmd_speech = [
            self.ffmpeg_path,
            "-y",
            "-i", str(video_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-acodec", "pcm_s16le",
            str(speech_audio_path)
        ]
        res = subprocess.run(cmd_speech, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {res.stderr}")

        # 2. High-quality master track
        if hq_audio_path:
            hq_audio_path.parent.mkdir(parents=True, exist_ok=True)
            cmd_hq = [
                self.ffmpeg_path,
                "-y",
                "-i", str(video_path),
                "-vn",
                "-ac", "2",
                "-ar", "48000",
                "-acodec", "pcm_s16le",
                str(hq_audio_path)
            ]
            res_hq = subprocess.run(cmd_hq, capture_output=True, text=True)
            if res_hq.returncode != 0:
                logger.warning("HQ audio extraction failed; will proceed with speech audio: %s", res_hq.stderr)

        return speech_audio_path, hq_audio_path

    def mux_video(
        self,
        video_path: Path,
        audio_path: Path,
        output_path: Path,
        subtitle_path: Path | None = None,
        language_code: str = "hi",
        target_duration: float | None = None
    ) -> Path:
        """Muxes the original video stream with the dubbed audio track.
        Preserves original video presentation timestamps and container duration.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
        ]

        if subtitle_path and subtitle_path.exists():
            cmd.extend(["-i", str(subtitle_path)])
            cmd.extend([
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-map", "2:s:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-c:s", "mov_text" if output_path.suffix.lower() == ".mp4" else "srt",
                "-metadata:s:s:0", f"language={language_code}",
            ])
        else:
            cmd.extend([
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
            ])

        if target_duration:
            cmd.extend(["-t", f"{target_duration:.3f}"])
        else:
            # Fallback to shortest stream among video/audio
            cmd.append("-shortest")

        cmd.append(str(output_path))

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("FFmpeg muxing failed: %s", res.stderr)
            raise RuntimeError(f"Muxing failed: {res.stderr}")

        return output_path
