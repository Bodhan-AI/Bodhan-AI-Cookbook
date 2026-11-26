import threading

from bodhan_dub.models.report import APIMetric


class CostAndLatencyTracker:
    """Thread-safe tracker for latency, retry counts, request metrics, and API cost observations."""

    def __init__(self):
        self._lock = threading.Lock()
        self.metrics: dict[str, APIMetric] = {
            "transcribe": APIMetric(
                service="Bodhan Indic-Transcribe",
                endpoint="POST /v1/audio/transcriptions",
                cost_notes="Billed per second of audio (rate depends on plan; marked unbilled in report)"
            ),
            "translate": APIMetric(
                service="Bodhan Indic-Translate",
                endpoint="POST /translate",
                cost_notes="Output tokens billed; input tokens free (marked unbilled in report)"
            ),
            "speak": APIMetric(
                service="Bodhan Indic-Speak",
                endpoint="POST /v1/audio/speech",
                cost_notes="Billed per character synthesized (marked unbilled in report)"
            ),
            "back_transcribe": APIMetric(
                service="Bodhan Indic-Transcribe (QA)",
                endpoint="POST /v1/audio/transcriptions",
                cost_notes="QA verification back-transcription"
            ),
        }

    def record_call(self, service: str, latency: float, retries: int = 0):
        with self._lock:
            if service not in self.metrics:
                self.metrics[service] = APIMetric(service=service, endpoint="unknown")

            m = self.metrics[service]
            m.call_count += 1
            m.total_latency_seconds += latency
            m.avg_latency_seconds = round(m.total_latency_seconds / max(1, m.call_count), 3)
            m.retry_count += retries

    def get_summary(self) -> dict[str, APIMetric]:
        with self._lock:
            return dict(self.metrics)
