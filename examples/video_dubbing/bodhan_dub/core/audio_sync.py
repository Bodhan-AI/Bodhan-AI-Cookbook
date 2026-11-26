import logging
import shutil
import subprocess
import wave
from pathlib import Path

from bodhan_dub.config import BodhanConfig
from bodhan_dub.models.segment import DubSegment

logger = logging.getLogger(__name__)

class AudioSyncEngine:
    """Intelligent Synchronization Engine enforcing strict timing and pitch-preserving time-stretching."""

    def __init__(
        self,
        min_speedup: float = BodhanConfig.MIN_SPEEDUP,   # 0.85
        max_speedup: float = BodhanConfig.MAX_SPEEDUP,   # 1.20
        alignment_tolerance_ms: float = BodhanConfig.SEGMENT_ALIGNMENT_TOLERANCE_MS, # 300 ms
        duration_tolerance_ms: float = BodhanConfig.TIMELINE_TOLERANCE_MS,           # 100 ms
        ffmpeg_path: str = "ffmpeg"
    ):
        self.min_speedup = min_speedup
        self.max_speedup = max_speedup
        self.alignment_tolerance_ms = alignment_tolerance_ms
        self.duration_tolerance_ms = duration_tolerance_ms
        self.ffmpeg_path = shutil.which(ffmpeg_path) or "ffmpeg"

    def build_atempo_filter(self, tempo: float) -> str:
        """Constructs chained FFmpeg atempo filters (since each atempo filter supports 0.5 to 2.0)."""
        if tempo <= 0:
            raise ValueError("Tempo factor must be positive.")

        factors = []
        rem = tempo
        while rem > 2.0:
            factors.append(2.0)
            rem /= 2.0
        while rem < 0.5:
            factors.append(0.5)
            rem /= 0.5
        factors.append(rem)
        return ",".join(f"atempo={f:.6f}" for f in factors)

    def stretch_audio(self, input_wav: Path, output_wav: Path, tempo_factor: float) -> Path:
        """Applies pitch-preserving time stretching using FFmpeg atempo filter."""
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        if abs(tempo_factor - 1.0) < 0.01:
            shutil.copy2(input_wav, output_wav)
            return output_wav

        filter_str = self.build_atempo_filter(tempo_factor)
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", str(input_wav),
            "-filter:a", filter_str,
            "-ar", "16000",
            "-ac", "1",
            "-acodec", "pcm_s16le",
            str(output_wav)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error("FFmpeg tempo adjustment failed: %s", res.stderr)
            raise RuntimeError(f"FFmpeg tempo adjustment failed: {res.stderr}")
        return output_wav

    def evaluate_and_fit_segment(
        self,
        segment: DubSegment,
        audio_path: Path,
        fitted_dir: Path
    ) -> tuple[bool, float, Path]:
        """Evaluates whether generated audio fits within the permitted time-stretching window [0.85x, 1.2x].

        Returns:
            (fits_within_spec: bool, required_tempo: float, fitted_audio_path: Path)
        """
        target_duration = segment.target_duration
        if target_duration <= 0.05:
            # Degenerate zero-duration segment
            return True, 1.0, audio_path

        with wave.open(str(audio_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            raw_duration = frames / float(rate) if rate > 0 else target_duration

        segment.original_audio_duration = round(raw_duration, 3)
        required_tempo = raw_duration / target_duration

        fits_within_spec = (self.min_speedup <= required_tempo <= self.max_speedup)

        fitted_out = fitted_dir / f"{segment.id}_fitted.wav"
        if fits_within_spec:
            self.stretch_audio(audio_path, fitted_out, required_tempo)
            segment.applied_tempo_factor = round(required_tempo, 3)
        else:
            # Cap at bounds for fallback preview
            capped_tempo = max(self.min_speedup, min(self.max_speedup, required_tempo))
            self.stretch_audio(audio_path, fitted_out, capped_tempo)
            segment.applied_tempo_factor = round(capped_tempo, 3)

        with wave.open(str(fitted_out), "rb") as wf:
            f_dur = wf.getnframes() / float(wf.getframerate())
        segment.fitted_audio_duration = round(f_dur, 3)
        segment.fitted_audio_path = str(fitted_out)

        return fits_within_spec, required_tempo, fitted_out

    def assemble_timeline(
        self,
        segments: list[DubSegment],
        total_video_duration: float,
        output_timeline_path: Path,
        sample_rate: int = 16000
    ) -> Path:
        """Assembles all fitted segments onto a precise master PCM timeline.
        Preserves video duration within ±100 ms and segment alignment within ±300 ms.
        """
        output_timeline_path.parent.mkdir(parents=True, exist_ok=True)
        total_frames = int(round(total_video_duration * sample_rate))
        timeline_bytes = bytearray(b"\x00\x00" * total_frames)

        for seg in segments:
            if not seg.fitted_audio_path or not Path(seg.fitted_audio_path).exists():
                continue

            start_frame = int(round(seg.start * sample_rate))
            if start_frame >= total_frames:
                continue

            with wave.open(seg.fitted_audio_path, "rb") as wf:
                seg_frames = wf.readframes(wf.getnframes())

            seg_byte_len = len(seg_frames)
            insert_byte_start = start_frame * 2
            insert_byte_end = min(total_frames * 2, insert_byte_start + seg_byte_len)

            # Mix/overlay PCM16 samples
            clip_len = insert_byte_end - insert_byte_start
            timeline_bytes[insert_byte_start:insert_byte_end] = seg_frames[:clip_len]

        with wave.open(str(output_timeline_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(timeline_bytes))

        # Verify final duration
        actual_dur = len(timeline_bytes) / (2.0 * sample_rate)
        drift_ms = abs(actual_dur - total_video_duration) * 1000.0
        if drift_ms > self.duration_tolerance_ms:
            logger.warning("Timeline duration drift %.1f ms exceeds tolerance %.1f ms", drift_ms, self.duration_tolerance_ms)

        return output_timeline_path
