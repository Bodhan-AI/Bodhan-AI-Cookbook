"""Dev credit ledger (outputs/credit_ledger.json, git-ignored). See CLAUDE.md: update it after every paid live call."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

DEFAULT_PATH = Path("outputs/credit_ledger.json")
LOW_CREDIT_INR = 2.0


def record(what: str, model: str, cost_inr: float, path: Path = DEFAULT_PATH) -> float:
    """Append an entry and update spent / remaining. Returns the remaining credit in rupees."""
    ledger = json.loads(path.read_text(encoding="utf-8"))
    ledger["entries"].append({"date": date.today().isoformat(), "what": what, "model": model, "cost_inr": cost_inr})
    ledger["spent_inr"] = round(sum(e["cost_inr"] for e in ledger["entries"]), 6)
    ledger["remaining_inr"] = round(ledger["total_credit_inr"] - ledger["spent_inr"], 6)
    path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ledger["remaining_inr"]


def remaining(path: Path = DEFAULT_PATH) -> float:
    """Credit left. Tell the user when this drops below LOW_CREDIT_INR before making a live call."""
    return json.loads(path.read_text(encoding="utf-8"))["remaining_inr"]
