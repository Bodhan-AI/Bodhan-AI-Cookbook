"""Budget tracking, pre-flight checks, and output path sandboxing for the agent.
Supports extracting and enforcing user-stated budget caps from task descriptions.
"""

import os
import re
from pathlib import Path
from typing import Optional
from bodhan_mcp.types import ToolError


def parse_stated_budget_inr(text: Optional[str]) -> Optional[float]:
    """Extract user-stated budget cap in INR from natural language task description.
    Supports phrases like:
    - '5 paise', '50 paise', '10 paisa' -> 0.05, 0.50, 0.10
    - 'under Rs 1', 'under Rs. 1', 'spend Rs 2' -> 1.0, 1.0, 2.0
    - 'under 1 rupee', 'spend 2 rupees' -> 1.0, 2.0
    - 'budget of ₹0.50', '₹1 budget' -> 0.50, 1.0
    """
    if not text:
        return None
    text_lower = text.lower()

    # 1. Check for paise: e.g. "5 paise", "10 paisa", "50 paise", "only want to spend 5 paise"
    paise_pattern = r"(?:spend|budget|under|cap|max(?:imum)?|at most|less than|only|within|limit(?: of)?)\s*(\d+(?:\.\d+)?)\s*(?:paise|paisa)\b"
    m_paise = re.search(paise_pattern, text_lower)
    if m_paise:
        try:
            return round(float(m_paise.group(1)) / 100.0, 4)
        except (ValueError, TypeError):
            pass

    # Standalone "N paise" / "N paisa"
    plain_paise = r"\b(\d+(?:\.\d+)?)\s*(?:paise|paisa)\b"
    m_plain = re.search(plain_paise, text_lower)
    if m_plain:
        try:
            return round(float(m_plain.group(1)) / 100.0, 4)
        except (ValueError, TypeError):
            pass

    # 2. Check for rupees with prefix: e.g. "under Rs 1", "spend ₹2", "budget of Rs. 0.5", "under INR 2"
    rs_prefix_pattern = r"(?:under|spend|budget|cap|max(?:imum)?|at most|less than|only|within|limit(?: of)?)\s*(?:of|is|:)?\s*(?:rs\.?|inr|₹)\s*(\d+(?:\.\d+)?)"
    m_rs_pre = re.search(rs_prefix_pattern, text_lower)
    if m_rs_pre:
        try:
            return round(float(m_rs_pre.group(1)), 4)
        except (ValueError, TypeError):
            pass

    # 3. Check for rupees with suffix: e.g. "under 1 rupee", "spend 2 rs", "budget 5 rupees"
    rs_suffix_pattern = r"(?:under|spend|budget|cap|max(?:imum)?|at most|less than|only|within|limit(?: of)?)\s*(?:of|is|:)?\s*(\d+(?:\.\d+)?)\s*(?:rs\.?|inr|₹|rupees?|rupee)\b"
    m_rs_suf = re.search(rs_suffix_pattern, text_lower)
    if m_rs_suf:
        try:
            return round(float(m_rs_suf.group(1)), 4)
        except (ValueError, TypeError):
            pass

    # 4. Direct symbol with number followed by budget keywords: e.g. "₹1 budget", "Rs 2 cap"
    direct_pattern = r"(?:rs\.?|inr|₹)\s*(\d+(?:\.\d+)?)\s*(?:budget|cap|limit|max)"
    m_dir = re.search(direct_pattern, text_lower)
    if m_dir:
        try:
            return round(float(m_dir.group(1)), 4)
        except (ValueError, TypeError):
            pass

    return None


class BudgetTracker:
    def __init__(
        self,
        out_dir: str,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        task: Optional[str] = None,
    ):
        self.out_dir = Path(out_dir).resolve()
        self.artifacts_dir = self.out_dir / "artifacts"
        self.max_steps = max_steps
        self.cli_max_cost_inr = max_cost_inr

        # User-stated budget detection & hard-cap enforcement
        self.user_stated_budget_inr = parse_stated_budget_inr(task) if task else None
        if self.user_stated_budget_inr is not None and self.user_stated_budget_inr < max_cost_inr:
            self.max_cost_inr = self.user_stated_budget_inr
            self.budget_source = "user_prompt"
        else:
            self.max_cost_inr = max_cost_inr
            self.budget_source = "cli_flag"

        self.current_step = 0
        self.spent_cost_inr = 0.0

        # Ensure sandboxed output directories exist
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def sandbox_path(self, filename: str) -> Path:
        """Resolve and ensure that an artifact output path is strictly sandboxed inside out/artifacts/."""
        clean_name = os.path.basename(filename)
        return (self.artifacts_dir / clean_name).resolve()

    def record_cost(self, cost_inr: float) -> None:
        """Record cost incurred from a step and enforce hard cap."""
        self.spent_cost_inr = round(self.spent_cost_inr + max(0.0, cost_inr), 4)
        if self.spent_cost_inr > self.max_cost_inr:
            cap_desc = f"₹{self.max_cost_inr:.4f}"
            if self.user_stated_budget_inr is not None:
                cap_desc += f" (user-stated cap)"
            raise ToolError(
                error_code="BUDGET_EXCEEDED",
                message=f"Spent cost (₹{self.spent_cost_inr:.4f}) exceeded budget cap ({cap_desc}). Stopping immediately.",
                retryable=False,
            )

    def check_preflight_budget(self, estimated_cost_inr: float) -> bool:
        """Check if an estimated cost would exceed remaining budget."""
        remaining = round(self.max_cost_inr - self.spent_cost_inr, 4)
        if estimated_cost_inr > remaining:
            cap_desc = f"₹{self.max_cost_inr:.4f}"
            if self.user_stated_budget_inr is not None:
                cap_desc += f" (user-stated cap)"
            raise ToolError(
                error_code="BUDGET_EXCEEDED",
                message=f"Estimated cost (₹{estimated_cost_inr:.4f}) exceeds remaining budget ({cap_desc}, remaining: ₹{remaining:.4f}). Stopping early and honestly.",
                retryable=False,
            )
        return True

    def check_step_limit(self) -> None:
        """Check if max steps limit has been reached."""
        self.current_step += 1
        if self.current_step > self.max_steps:
            raise ToolError(
                error_code="STEP_LIMIT_EXCEEDED",
                message=f"Maximum allowed steps ({self.max_steps}) reached. Stopping cleanly.",
                retryable=False,
            )
