"""Budget enforcement and duplicate call suppression for Bodhan Task Agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pymupdf

from bodhan_mcp.common import (
    PRICE_OCR_PER_PAGE_INR,
    PRICE_SPEAK_PER_10K_INPUT_CHARS_INR,
    PRICE_TRANSCRIBE_PER_AUDIO_MINUTE_INR,
    PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS_INR,
    total_spend_inr,
)
from bodhan_mcp.tools.transcribe import get_ffmpeg_binaries, probe_audio_duration


class BudgetManager:
    """Tracks cumulative expenditure against max_cost_inr, evaluates pre-flight costs,
    and suppresses duplicate tool invocations.
    """

    def __init__(self, max_cost_inr: float = 20.0, run_id: Optional[str] = None):
        self.max_cost_inr = max_cost_inr
        self.run_id = run_id
        self.spent_cost_inr = 0.0
        self._duplicate_cache: Dict[str, Dict[str, Any]] = {}

    def get_spent_cost(self) -> float:
        """Returns total cost spent in INR for this run_id."""
        if self.run_id:
            ledger_spend = total_spend_inr(run_id=self.run_id)
            return max(self.spent_cost_inr, ledger_spend)
        return round(self.spent_cost_inr, 6)

    def get_remaining_budget(self) -> float:
        """Returns remaining available budget in INR for this run_id."""
        return max(0.0, round(self.max_cost_inr - self.get_spent_cost(), 6))

    def estimate_cost(self, tool_name: str, args: Dict[str, Any]) -> float:
        """Calculates pre-flight estimated cost in INR without contacting live APIs."""
        if tool_name == "indic_translate":
            text = str(args.get("text", ""))
            # Heuristic: ~2 chars per token
            est_tokens = max(1, len(text) // 2)
            cost = (est_tokens / 10000.0) * PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS_INR
            return round(cost, 6)

        if tool_name == "indic_ocr":
            file_path_str = str(args.get("file_path", ""))
            path_obj = Path(file_path_str)
            if path_obj.exists() and path_obj.suffix.lower() == ".pdf":
                try:
                    doc = pymupdf.open(str(path_obj))
                    pages = min(args.get("max_pages", 10), doc.page_count)
                    doc.close()
                    return round(pages * PRICE_OCR_PER_PAGE_INR, 4)
                except Exception:
                    pass
            # Default single image or fallback
            return round(PRICE_OCR_PER_PAGE_INR, 4)

        if tool_name == "indic_transcribe":
            file_path_str = str(args.get("file_path", ""))
            path_obj = Path(file_path_str)
            if path_obj.exists():
                _, ffprobe_bin = get_ffmpeg_binaries()
                if ffprobe_bin:
                    dur, err = probe_audio_duration(path_obj, ffprobe_bin)
                    if not err and dur and dur > 0:
                        return round((dur / 60.0) * PRICE_TRANSCRIBE_PER_AUDIO_MINUTE_INR, 6)
            # Default estimate for short sample
            return round(0.5 * PRICE_TRANSCRIBE_PER_AUDIO_MINUTE_INR, 6)

        if tool_name == "indic_speak":
            input_text = str(args.get("input_text") or args.get("text") or "")
            chars = len(input_text)
            return round((chars / 10000.0) * PRICE_SPEAK_PER_10K_INPUT_CHARS_INR, 6)

        # Free local tools
        return 0.0

    def can_afford(self, tool_name: str, args: Dict[str, Any]) -> Tuple[bool, float, Optional[str]]:
        """Checks if executing the tool would exceed the remaining budget limit."""
        est_cost = self.estimate_cost(tool_name, args)
        current_spent = self.get_spent_cost()
        if current_spent + est_cost > self.max_cost_inr:
            msg = (
                f"BUDGET_EXCEEDED: Tool '{tool_name}' estimated cost Rs {est_cost:.4f} "
                f"would exceed run budget ceiling of Rs {self.max_cost_inr:.2f} "
                f"(already spent in run: Rs {current_spent:.4f})."
            )
            return False, est_cost, msg
        return True, est_cost, None

    def can_afford_verification(self, est_cost: float) -> bool:
        """Checks if verification cost can be afforded within remaining budget."""
        return (self.get_spent_cost() + est_cost) <= self.max_cost_inr

    def make_call_signature(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Generates deterministic key for duplicate detection."""
        try:
            serialized_args = json.dumps(args, sort_keys=True, ensure_ascii=False)
        except Exception:
            serialized_args = str(sorted(args.items()))
        return f"{tool_name}::{serialized_args}"

    def check_duplicate(self, tool_name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Returns earlier tool result if identical tool call was already executed."""
        sig = self.make_call_signature(tool_name, args)
        return self._duplicate_cache.get(sig)

    def record_actual_execution(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result_data: Dict[str, Any],
    ) -> float:
        """Records expenditure from actual tool output and caches result for duplicate suppression."""
        if result_data.get("cache_hit") is True:
            actual_cost = 0.0
        else:
            actual_cost = float(result_data.get("estimated_cost_inr", 0.0))
        self.spent_cost_inr += actual_cost

        sig = self.make_call_signature(tool_name, args)
        self._duplicate_cache[sig] = result_data

        return actual_cost
