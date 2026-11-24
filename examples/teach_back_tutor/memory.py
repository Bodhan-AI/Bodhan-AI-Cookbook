"""Cross-session student memory: what the tutor remembers about a student between sessions.

One JSON file per student. After each session we store what they struggled with; the next session
starts from there: at the level they reached, probing their weak concepts first, and re-planting the
mistakes they missed to check the correction stuck. All of this is plain Python, no LLM.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"


def _path(student: str) -> Path:
    slug = re.sub(r"[^a-z0-9_-]+", "_", student.strip().lower()).strip("_") or "student"
    return PROFILES_DIR / f"{slug}.json"


def load(student: str) -> dict:
    path = _path(student)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"student": student, "level": None, "topics": {}, "sessions": []}


def save(profile: dict) -> Path:
    PROFILES_DIR.mkdir(exist_ok=True)
    path = _path(profile["student"])
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def for_session(profile: dict, topic: str) -> dict:
    """What the next session should know. Empty for a new student."""
    if not profile["sessions"]:
        return {}
    seen = profile["topics"].get(topic, {})
    return {
        "level": profile["level"],
        "last_topic": profile["sessions"][-1]["topic"],
        "same_topic": bool(seen),
        "weak_concepts": seen.get("weak_concepts", []),
        "retest_plants": seen.get("missed_plants", []),
        "previous_mastery": seen.get("mastery"),
    }


def record(profile: dict, session, rep: dict) -> dict:
    """Fold a finished session (tutor.Session + tutor.report) into the profile."""
    card = session.card
    weak = [c["name"] for c in rep["concepts"] if c["status"] != "explained"]
    # missed, needed a hint, or never answered (session ended early): plant it again next time
    missed = [card_m["wrong_statement"] for card_m in card["misconceptions"]
              if session.plants[card_m["id"]]["status"] in ("revealed", "caught_after_hint", "planted")]
    plants = rep["planted"]
    profile["level"] = session.level
    profile["topics"][card["topic"]] = {
        "mastery": rep["mastery"],
        "weak_concepts": weak,
        "missed_plants": missed,
        "open_errors": [e["claim"] for e in rep["student_errors"] if not e["resolved"]],
    }
    profile["sessions"].append({
        "date": date.today().isoformat(),
        "topic": card["topic"],
        "band": session.band,
        "mastery": rep["mastery"],
        "level_end": session.level,
        "plants_caught": sum(p["outcome"] == "caught" for p in plants),
        "plants_total": len(plants),
        "avg_quality": rep["avg_quality"],
    })
    return profile
