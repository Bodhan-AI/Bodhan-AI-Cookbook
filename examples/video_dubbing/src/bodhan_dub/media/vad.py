"""
Voice Activity Detection (VAD) and speech/pause boundary detection.
Primary engine: Silero-VAD via onnxruntime.
Fallback engine: ffmpeg silencedetect filter.
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf

from bodhan_dub.config import SILERO_VAD_PINNED_SHA256

logger = logging.getLogger("bodhan_dub.vad")


@dataclass
class TimeRange:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class SpeechRegion(TimeRange):
    pass


@dataclass
class Pause(TimeRange):
    pass


class VADResult:
    def __init__(
        self,
        total_duration: float,
        speech_regions: List[SpeechRegion],
        pauses: List[Pause],
        engine_used: str = "silero_onnx",
    ):
        self.total_duration = total_duration
        self.speech_regions = speech_regions
        self.pauses = pauses
        self.engine_used = engine_used

    @property
    def has_speech(self) -> bool:
        return len(self.speech_regions) > 0 and sum(r.duration for r in self.speech_regions) >= 0.3

    @property
    def total_speech_duration(self) -> float:
        return sum(r.duration for r in self.speech_regions)


def estimate_adaptive_noise_floor(wav_path: Path) -> float:
    """
    B5: Computes adaptive percentile-based noise floor from audio frames.
    Clamped between -45 dBFS and -24 dBFS. Prevents background music or ambient noise
    from being misclassified as speech.
    """
    try:
        data, sr = sf.read(str(wav_path), dtype="float32")
        if len(data) == 0:
            return -32.0
        frame_len = max(1, int(0.100 * sr))
        n_frames = len(data) // frame_len
        if n_frames < 5:
            return -32.0
        rms = np.array([np.sqrt(np.mean(data[i * frame_len : (i + 1) * frame_len] ** 2)) for i in range(n_frames)])
        db = 20 * np.log10(np.maximum(1e-9, rms))
        floor_db = float(np.percentile(db, 20))
        adaptive_th = float(np.clip(floor_db + 6.0, -45.0, -24.0))
        return round(adaptive_th, 1)
    except Exception:
        return -32.0


def detect_silence_ffmpeg(
    wav_path: Path,
    noise_db: Optional[float] = None,
    min_silence_duration: float = 0.3,
) -> Tuple[List[Pause], float]:
    """
    Runs ffmpeg silencedetect to find pauses in audio using adaptive or explicit noise floor.
    Returns (list_of_pauses, total_duration).
    """
    if noise_db is None:
        noise_db = estimate_adaptive_noise_floor(wav_path)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(wav_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_duration}",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)

    pauses: List[Pause] = []
    current_start: Optional[float] = None
    duration = 0.0

    # Parse duration from ffprobe or ffmpeg output
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr)
    if dur_match:
        hours = float(dur_match.group(1))
        mins = float(dur_match.group(2))
        secs = float(dur_match.group(3))
        duration = hours * 3600 + mins * 60 + secs

    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            m = re.search(r"silence_start:\s*([0-9\.]+)", line)
            if m:
                current_start = float(m.group(1))
        elif "silence_end:" in line and current_start is not None:
            m = re.search(r"silence_end:\s*([0-9\.]+)", line)
            if m:
                end = float(m.group(1))
                pauses.append(Pause(start=round(current_start, 3), end=round(end, 3)))
                current_start = None

    # If audio ended while inside silence
    if current_start is not None and duration > current_start:
        pauses.append(Pause(start=round(current_start, 3), end=round(duration, 3)))

    return pauses, duration


def invert_pauses_to_speech(pauses: List[Pause], total_duration: float) -> List[SpeechRegion]:
    """Inverts silence intervals into speech regions."""
    speech: List[SpeechRegion] = []
    current_pos = 0.0

    # Sort pauses by start time
    sorted_pauses = sorted(pauses, key=lambda p: p.start)

    for pause in sorted_pauses:
        if pause.start > current_pos + 0.05:  # at least 50ms speech
            speech.append(SpeechRegion(start=round(current_pos, 3), end=round(pause.start, 3)))
        current_pos = max(current_pos, pause.end)

    if total_duration > current_pos + 0.05:
        speech.append(SpeechRegion(start=round(current_pos, 3), end=round(total_duration, 3)))

    return speech


SILERO_VAD_DOWNLOAD_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"
)


def fetch_silero_vad_model(dest_path: Path) -> bool:
    """
    B5: Pinned SHA-256 verified download of silero_vad.onnx model.
    """
    import urllib.request

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = dest_path.with_suffix(".tmp")
        urllib.request.urlretrieve(SILERO_VAD_DOWNLOAD_URL, str(temp_path))
        with open(temp_path, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        if h == SILERO_VAD_PINNED_SHA256:
            temp_path.replace(dest_path)
            logger.info(f"Successfully downloaded and verified Silero VAD model to '{dest_path}'")
            return True
        else:
            logger.warning(f"Downloaded model SHA-256 mismatch ({h} != {SILERO_VAD_PINNED_SHA256})")
            temp_path.unlink(missing_ok=True)
            return False
    except Exception as e:
        logger.warning(f"Could not download Silero VAD model: {e}")
        return False


class SileroVADDetector:
    """ONNX-based Silero VAD implementation with automatic ffmpeg fallback."""

    def __init__(self, model_path: Optional[Path] = None):
        self.model_path = model_path
        self._session = None
        self._try_load_model()

    def _try_load_model(self) -> None:
        """Attempts to load Silero VAD onnx session with SHA256 integrity verification."""
        try:
            import onnxruntime as ort

            # C5/B5: Look for silero_vad.onnx in: explicit path, assets/models/, package directory, current directory
            candidates = [
                self.model_path,
                Path("assets/models/silero_vad.onnx"),
                Path(__file__).resolve().parent.parent / "assets" / "models" / "silero_vad.onnx",
                Path(__file__).resolve().parent.parent.parent / "silero_vad.onnx",
                Path("silero_vad.onnx"),
            ]
            valid_path = None
            for c in candidates:
                if c and Path(c).exists():
                    valid_path = Path(c)
                    break

            if not valid_path:
                target_asset = Path("assets/models/silero_vad.onnx")
                if fetch_silero_vad_model(target_asset):
                    valid_path = target_asset
                else:
                    logger.info("Silero VAD model not available locally or via download. Using ffmpeg silencedetect.")
                    self._session = None
                    return

            # Verify SHA256 integrity
            try:
                with open(valid_path, "rb") as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()
                if file_hash != SILERO_VAD_PINNED_SHA256:
                    logger.warning(
                        f"Silero VAD model at '{valid_path}' failed SHA256 integrity check "
                        f"(expected {SILERO_VAD_PINNED_SHA256}, got {file_hash}). "
                        "Falling back cleanly to ffmpeg silencedetect."
                    )
                    self._session = None
                    return
            except Exception as e:
                logger.warning(
                    f"Error checking Silero VAD integrity at '{valid_path}': {e}. "
                    "Falling back cleanly to ffmpeg silencedetect."
                )
                self._session = None
                return

            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self._session = ort.InferenceSession(
                str(valid_path), opts, providers=["CPUExecutionProvider"]
            )
        except Exception as e:
            logger.warning(
                f"Failed to initialize onnxruntime session for Silero VAD: {e}. Falling back cleanly to ffmpeg."
            )
            self._session = None

    def process(
        self,
        wav_path: Path,
        threshold: float = 0.5,
        min_silence_ms: int = 300,
        min_speech_ms: int = 250,
    ) -> VADResult:
        """
        Runs Voice Activity Detection on a 16kHz mono WAV file.
        Uses ONNX session if loaded, else falls back to ffmpeg silencedetect.
        """
        wav_path = Path(wav_path)
        if not wav_path.exists():
            raise FileNotFoundError(f"WAV file not found: {wav_path}")

        # If ONNX session is active, run native frame-level inference
        if self._session is not None:
            try:
                audio, sr = sf.read(str(wav_path))
                if sr != 16000:
                    raise ValueError(f"Silero VAD requires 16000 Hz, got {sr}")
                if len(audio.shape) > 1:
                    audio = audio[:, 0]
                audio = audio.astype(np.float32)
                total_duration = len(audio) / 16000.0

                # Silero window is 512 samples at 16kHz (32ms)
                window_size = 512
                state = np.zeros((2, 1, 128), dtype=np.float32)

                speech_frames: List[bool] = []
                for i in range(0, len(audio) - window_size + 1, window_size):
                    chunk = audio[i : i + window_size].reshape(1, -1)
                    inputs = {
                        "input": chunk,
                        "state": state,
                        "sr": np.array([16000], dtype=np.int64),
                    }
                    out, state = self._session.run(None, inputs)
                    prob = float(out[0][0])
                    speech_frames.append(prob >= threshold)

                # Convert boolean frames to regions
                frame_dur = window_size / 16000.0
                regions: List[SpeechRegion] = []
                in_speech = False
                speech_start = 0.0

                for idx, is_sp in enumerate(speech_frames):
                    t = idx * frame_dur
                    if is_sp and not in_speech:
                        in_speech = True
                        speech_start = t
                    elif not is_sp and in_speech:
                        in_speech = False
                        if t - speech_start >= (min_speech_ms / 1000.0):
                            regions.append(SpeechRegion(round(speech_start, 3), round(t, 3)))

                if in_speech and total_duration - speech_start >= (min_speech_ms / 1000.0):
                    regions.append(SpeechRegion(round(speech_start, 3), round(total_duration, 3)))

                # Derive pauses
                pauses: List[Pause] = []
                curr = 0.0
                for r in regions:
                    if r.start - curr >= (min_silence_ms / 1000.0):
                        pauses.append(Pause(round(curr, 3), round(r.start, 3)))
                    curr = r.end
                if total_duration - curr >= (min_silence_ms / 1000.0):
                    pauses.append(Pause(round(curr, 3), round(total_duration, 3)))

                return VADResult(
                    total_duration=round(total_duration, 3),
                    speech_regions=regions,
                    pauses=pauses,
                    engine_used="silero_onnx",
                )
            except Exception:
                # Fall back to ffmpeg silencedetect on any runtime error
                pass

        # Fallback Engine: ffmpeg silencedetect (with adaptive noise floor)
        pauses, duration = detect_silence_ffmpeg(
            wav_path,
            noise_db=None,
            min_silence_duration=min_silence_ms / 1000.0,
        )
        if duration <= 0.0:
            info = sf.info(str(wav_path))
            duration = info.duration

        speech_regions = invert_pauses_to_speech(pauses, duration)

        return VADResult(
            total_duration=round(duration, 3),
            speech_regions=speech_regions,
            pauses=pauses,
            engine_used="ffmpeg_silencedetect",
        )

    async def async_process(
        self,
        wav_path: Path,
        threshold: float = 0.5,
        min_speech_ms: float = 250.0,
        min_silence_ms: float = 200.0,
    ) -> VADResult:
        """B8: Offloads VAD processing to a background thread to keep event loop responsive."""
        import asyncio
        return await asyncio.to_thread(
            self.process,
            wav_path,
            threshold=threshold,
            min_speech_ms=min_speech_ms,
            min_silence_ms=min_silence_ms,
        )
