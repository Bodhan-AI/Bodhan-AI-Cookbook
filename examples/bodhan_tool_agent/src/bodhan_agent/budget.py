"""Budget management, pre-flight estimation, and cost enforcement (Constraint C9)."""


from bodhan_agent.resolver import FileRef
from bodhan_mcp import config


class BudgetManager:
    """Tracks and enforces budget ceilings across pre-flight and step execution."""

    def __init__(self, max_cost_inr: float = 20.0):
        self.max_cost_inr = float(max_cost_inr)
        self.spent_inr: float = 0.0

    @property
    def remaining_inr(self) -> float:
        """Remaining balance in INR."""
        return max(0.0, round(self.max_cost_inr - self.spent_inr, 4))

    def can_afford(self, next_step_cost: float) -> bool:
        """Check if the proposed step cost can be afforded within remaining budget."""
        return (self.spent_inr + next_step_cost) <= (self.max_cost_inr + 1e-6)

    def charge(self, cost_inr: float) -> None:
        """Record incurred cost against the budget."""
        self.spent_inr = round(self.spent_inr + cost_inr, 4)

    def estimate(self, selected_files: list[FileRef], task: str) -> float:
        """Pre-flight cost estimation based strictly on metadata and published prices (no model calls).

        Includes verification allowances and 10% safety buffer.
        """
        task_lower = task.lower()
        est = 0.0

        for f in selected_files:
            # Audio transcription cost
            if f.kind == "audio" and (
                "transcribe" in task_lower or "speech" in task_lower or "audio" in task_lower or "srt" in task_lower
            ):
                dur_s = f.duration_s or 60.0
                est += (dur_s / 60.0) * config.PRICES["transcribe_per_audio_min"]

            # OCR cost
            if f.kind in ("pdf", "image") and (
                "ocr" in task_lower or "read" in task_lower or "summarize" in task_lower or "translate" in task_lower
            ):
                pages = f.pages or 1
                est += pages * config.PRICES["ocr_per_image"]

        # Translation allowance
        if "translate" in task_lower or "tamil" in task_lower or "hindi" in task_lower or "english" in task_lower:
            # Estimate ~2,000 tokens for translation
            est += (2000 / 10000.0) * config.PRICES["translate_per_10k_output_tokens"]

        # TTS speech synthesis allowance
        if "speak" in task_lower or "voice" in task_lower or "read aloud" in task_lower or "audio version" in task_lower:
            # Estimate ~1,500 characters
            tts_cost = (1500 / 10000.0) * config.PRICES["speak_per_10k_chars"]
            est += tts_cost
            # Verification: back-transcription of generated audio (~1.5 minutes)
            est += 1.5 * config.PRICES["transcribe_per_audio_min"]

        # 10% safety buffer per AGENT_DESIGN.md §Budget model
        return round(est * 1.10, 4)
