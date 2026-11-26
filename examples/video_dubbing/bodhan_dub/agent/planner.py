import logging

from bodhan_dub.models.segment import DubSegment

logger = logging.getLogger(__name__)

class PipelinePlanner:
    """Agentic planner managing execution decisions, state transitions, and retry policies."""

    def __init__(self, max_condensation_retries: int = 2):
        self.max_condensation_retries = max_condensation_retries

    def plan_segment_execution(self, segments: list[DubSegment]) -> list[DubSegment]:
        """Initializes planning metadata and ordering for segments."""
        logger.info("Planner: scheduled %d segments for pipeline processing.", len(segments))
        for seg in segments:
            seg.metadata["plan_status"] = "SCHEDULED"
            seg.metadata["condensation_attempts"] = 0
        return segments

    def should_condense(self, segment: DubSegment) -> bool:
        """Determines if a segment needs English-side condensation based on timing violations."""
        attempts = segment.metadata.get("condensation_attempts", 0)
        if attempts >= self.max_condensation_retries:
            logger.warning("Segment %s reached max condensation retries (%d). Flagging for manual review.",
                           segment.id, self.max_condensation_retries)
            return False

        # If the applied tempo factor exceeds 1.20x (speech is overlong by > 20%)
        if segment.applied_tempo_factor > 1.20:
            return True
        return False
