import logging
import re
import unicodedata
import wave
from pathlib import Path
from typing import Any

from bodhan_dub.models.segment import DubSegment

logger = logging.getLogger(__name__)

class QualityAssuranceVerifier:
    """Automated Quality Verification Engine utilizing Bodhan Indic-Transcribe back-transcription,
    Indic Unicode normalization, Levenshtein distance, CER calculation, and audio artifact detection.
    """

    @staticmethod
    def normalize_indic_text(text: str) -> str:
        """Normalizes Indic text using Unicode NFC form, strips punctuation and extra whitespaces."""
        if not text:
            return ""
        # Canonical Unicode normalization
        norm = unicodedata.normalize("NFC", text)
        # Remove standard punctuation and symbols, preserve Indic script letters and numbers
        cleaned = re.sub(r"[^\w\s\u0900-\u0D7F]", " ", norm, flags=re.UNICODE)
        # Collapse multiple whitespace
        return re.sub(r"\s+", " ", cleaned).strip().lower()

    @classmethod
    def calculate_levenshtein(cls, s1: str, s2: str) -> int:
        """Computes true Levenshtein distance between two Unicode strings."""
        if len(s1) < len(s2):
            return cls.calculate_levenshtein(s2, s1)
        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]

    @classmethod
    def calculate_cer(cls, reference: str, hypothesis: str) -> float:
        """Calculates Character Error Rate (CER) = Levenshtein(ref, hyp) / max(len(ref), 1)."""
        norm_ref = cls.normalize_indic_text(reference)
        norm_hyp = cls.normalize_indic_text(hypothesis)

        if not norm_ref and not norm_hyp:
            return 0.0
        if not norm_ref:
            return 1.0

        dist = cls.calculate_levenshtein(norm_ref, norm_hyp)
        return min(1.0, dist / float(len(norm_ref)))

    def inspect_audio_artifacts(self, wav_path: Path) -> dict[str, Any]:
        """Checks for audio defects: silence, clipping (saturation), and truncated duration."""
        if not wav_path.exists():
            return {"error": "File does not exist", "is_silent": True, "is_clipped": False}

        try:
            with wave.open(str(wav_path), "rb") as wf:
                rate = wf.getframerate()
                nframes = wf.getnframes()
                if nframes == 0 or rate == 0:
                    return {"is_silent": True, "is_clipped": False, "duration": 0.0}

                raw = wf.readframes(nframes)
                duration = nframes / float(rate)

            # Analyze PCM16 sample values
            import struct
            sample_count = len(raw) // 2
            if sample_count == 0:
                return {"is_silent": True, "is_clipped": False, "duration": 0.0}

            samples = struct.unpack(f"<{sample_count}h", raw)
            max_amp = max(abs(s) for s in samples)
            clipped_samples = sum(1 for s in samples if abs(s) >= 32760)

            is_silent = (max_amp < 150)
            is_clipped = (clipped_samples > 10)

            return {
                "duration": duration,
                "is_silent": is_silent,
                "is_clipped": is_clipped,
                "max_amplitude": max_amp,
                "clipped_samples": clipped_samples
            }
        except Exception as e:
            logger.warning("Audio artifact inspection error on %s: %s", wav_path, e)
            return {"error": str(e), "is_silent": False, "is_clipped": False}

    def verify_segment(
        self,
        segment: DubSegment,
        audio_path: Path,
        back_transcription: str,
        cer_threshold: float = 0.40
    ) -> DubSegment:
        """Runs complete QA verification on a dubbed segment."""
        segment.back_transcription = back_transcription
        cer = self.calculate_cer(segment.translated_text, back_transcription)
        segment.cer = round(cer, 4)
        segment.levenshtein_distance = self.calculate_levenshtein(
            self.normalize_indic_text(segment.translated_text),
            self.normalize_indic_text(back_transcription)
        )

        artifacts = self.inspect_audio_artifacts(audio_path)
        reasons: list[str] = []

        if artifacts.get("is_silent"):
            reasons.append("Audio is completely silent or energy is below threshold.")
        if artifacts.get("is_clipped"):
            reasons.append("Audio has clipped PCM samples exceeding dynamic ceiling.")

        # Check alignment / timing
        if segment.applied_tempo_factor > 1.20:
            reasons.append(f"Tempo factor {segment.applied_tempo_factor}x exceeds 1.20x limit.")
        elif segment.applied_tempo_factor < 0.85:
            reasons.append(f"Tempo factor {segment.applied_tempo_factor}x is below 0.85x limit.")

        # Check CER
        if cer > cer_threshold:
            reasons.append(f"CER {cer:.2f} exceeds threshold {cer_threshold:.2f} (possible ASR divergence or speech defect).")

        segment.qa_reasons = reasons

        if not reasons:
            segment.qa_status = "ACCEPTED"
        elif any("silent" in r or "clipped" in r for r in reasons):
            segment.qa_status = "RETRY_AUDIO"
        elif any("exceeds 1.20x" in r for r in reasons):
            segment.qa_status = "REQUIRES_CONDENSATION"
        else:
            segment.qa_status = "MANUAL_REVIEW"

        return segment
