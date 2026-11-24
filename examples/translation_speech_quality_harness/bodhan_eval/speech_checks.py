import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import soundfile as sf


class SpeechQualityChecker:
    """
    Evaluates audio quality and compares speech transcription
    against the expected text.
    """

    def __init__(
        self,
        min_duration=0.3,
        max_duration=30.0,
        silence_threshold_db=-45.0,
        max_silence_ratio=0.50,
        clipping_threshold=0.999,
        max_clipping_ratio=0.001,
        text_similarity_threshold=0.70
    ):
        self.min_duration = min_duration
        self.max_duration = max_duration
        self.silence_threshold_db = silence_threshold_db
        self.max_silence_ratio = max_silence_ratio
        self.clipping_threshold = clipping_threshold
        self.max_clipping_ratio = max_clipping_ratio
        self.text_similarity_threshold = text_similarity_threshold

    @staticmethod
    def normalize_text(text):
        text = unicodedata.normalize("NFKC", text or "")
        text = text.lower()
        text = re.sub(r"[^\w\s]", "", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def calculate_similarity(expected, actual):
        expected = SpeechQualityChecker.normalize_text(expected)
        actual = SpeechQualityChecker.normalize_text(actual)

        if not expected or not actual:
            return 0.0

        return round(
            SequenceMatcher(None, expected, actual).ratio(),
            4
        )

    @staticmethod
    def detect_repetition(text):
        """Detect consecutive repeated words and short phrases."""
        words = SpeechQualityChecker.normalize_text(text).split()
        repetitions = []

        # Repeated individual words
        for i in range(1, len(words)):
            if words[i] == words[i - 1]:
                repetitions.append(words[i])

        # Repeated two-word phrases
        for i in range(3, len(words)):
            previous_pair = words[i - 3:i - 1]
            current_pair = words[i - 1:i + 1]

            if previous_pair == current_pair:
                phrase = " ".join(current_pair)
                repetitions.append(phrase)

        return sorted(set(repetitions))

    def analyze_audio(self, audio_path):
        """Calculate duration, silence and clipping metrics."""

        path = Path(audio_path)

        if not path.exists():
            return {
                "status": "failed",
                "errors": ["Audio file does not exist."]
            }

        try:
            audio, sample_rate = sf.read(
                str(path),
                dtype="float32",
                always_2d=True
            )
        except Exception as exc:
            return {
                "status": "failed",
                "errors": [f"Unable to read audio: {exc}"]
            }

        if audio.size == 0 or sample_rate <= 0:
            return {
                "status": "failed",
                "errors": ["Audio file is empty or invalid."]
            }

        duration = len(audio) / sample_rate

        # Convert stereo or multi-channel audio to mono for analysis.
        mono = np.mean(audio, axis=1)

        # Divide audio into approximately 20 ms frames.
        frame_size = max(1, int(sample_rate * 0.02))

        frame_rms = []

        for start in range(0, len(mono), frame_size):
            frame = mono[start:start + frame_size]

            if len(frame) == 0:
                continue

            rms = np.sqrt(np.mean(frame ** 2))
            frame_rms.append(rms)

        frame_rms = np.array(frame_rms)

        # Convert silence threshold from dB to amplitude.
        silence_threshold = 10 ** (
            self.silence_threshold_db / 20
        )

        silent_frames = frame_rms < silence_threshold

        silence_ratio = float(
            np.mean(silent_frames)
        )

        clipping_ratio = float(
            np.mean(np.abs(mono) >= self.clipping_threshold)
        )

        errors = []
        warnings = []

        if duration < self.min_duration:
            errors.append("Audio duration is too short.")

        if duration > self.max_duration:
            errors.append("Audio duration exceeds the configured limit.")

        if silence_ratio > self.max_silence_ratio:
            warnings.append("Excessive silence detected.")

        if clipping_ratio > self.max_clipping_ratio:
            warnings.append("Possible audio clipping detected.")

        status = "passed"

        if errors:
            status = "failed"
        elif warnings:
            status = "warning"

        return {
            "status": status,
            "duration_seconds": round(duration, 3),
            "sample_rate": int(sample_rate),
            "channels": int(audio.shape[1]),
            "silence_ratio": round(silence_ratio, 4),
            "clipping_ratio": round(clipping_ratio, 6),
            "errors": errors,
            "warnings": warnings
        }

    def compare_transcription(self, expected_text, actual_text):
        """Compare expected speech with ASR transcription."""

        similarity = self.calculate_similarity(
            expected_text,
            actual_text
        )

        repetitions = self.detect_repetition(actual_text)

        warnings = []

        if similarity < self.text_similarity_threshold:
            warnings.append(
                "Possible speech omission or transcription mismatch."
            )

        if repetitions:
            warnings.append(
                "Repeated words or phrases detected."
            )

        return {
            "status": "warning" if warnings else "passed",
            "similarity": similarity,
            "repetitions": repetitions,
            "expected_text": expected_text,
            "transcribed_text": actual_text,
            "warnings": warnings
        }

    def evaluate(self, audio_path, expected_text, actual_text):
        """Run all speech-quality checks."""

        audio_result = self.analyze_audio(audio_path)

        if audio_result["status"] == "failed":
            return {
                "status": "failed",
                "audio": audio_result,
                "transcription": None
            }

        transcription_result = self.compare_transcription(
            expected_text,
            actual_text
        )

        if audio_result["status"] == "warning":
            status = "warning"
        else:
            status = transcription_result["status"]

        return {
            "status": status,
            "audio": audio_result,
            "transcription": transcription_result
        }