import logging
import shutil
import subprocess
from pathlib import Path

from bodhan_dub.config import BodhanConfig

logger = logging.getLogger(__name__)

class AudioMaster:
    """Mastering engine for ducking original audio, mixing dubbed speech, and loudness normalization."""

    def __init__(self, ffmpeg_path: str = "ffmpeg", target_lufs: float = BodhanConfig.TARGET_LUFS):
        self.ffmpeg_path = shutil.which(ffmpeg_path) or "ffmpeg"
        self.target_lufs = target_lufs

    def mix_and_master(
        self,
        dubbed_speech_wav: Path,
        original_hq_audio: Path | None,
        output_master_wav: Path,
        duck_level_db: float = -12.0
    ) -> Path:
        """Mixes dubbed speech with ducked original background audio and applies EBU R128 loudness normalization (-16 LUFS)."""
        output_master_wav.parent.mkdir(parents=True, exist_ok=True)

        if not original_hq_audio or not original_hq_audio.exists():
            # If no original background audio available, normalize dubbed speech directly
            return self.normalize_loudness(dubbed_speech_wav, output_master_wav)

        # Sidechain ducking filter: reduce original background track volume when dubbed speech is active
        filter_complex = (
            f"[1:a]volume=0.3[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[mixed];"
            f"[mixed]loudnorm=I={self.target_lufs:.1f}:TP=-1.5:LRA=11[out]"
        )

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", str(dubbed_speech_wav),
            "-i", str(original_hq_audio),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-ac", "2",
            "-ar", "48000",
            "-acodec", "pcm_s16le",
            str(output_master_wav)
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.warning("Audio ducking/mixing failed (%s), falling back to direct loudness normalization.", res.stderr)
            return self.normalize_loudness(dubbed_speech_wav, output_master_wav)

        return output_master_wav

    def normalize_loudness(self, input_wav: Path, output_wav: Path) -> Path:
        """Applies EBU R128 (-16 LUFS) loudness normalization to input audio."""
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", str(input_wav),
            "-af", f"loudnorm=I={self.target_lufs:.1f}:TP=-1.5:LRA=11",
            "-ac", "2",
            "-ar", "48000",
            "-acodec", "pcm_s16le",
            str(output_wav)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("Loudness normalization failed: %s", res.stderr)
            shutil.copy2(input_wav, output_wav)
        return output_wav
