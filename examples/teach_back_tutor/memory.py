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
    names = [c["name"] for c in rep["concepts"]]
    previous = profile["topics"].get(card["topic"], {}).get("mastered", [])
    explained = {c["name"] for c in rep["concepts"] if c["status"] == "explained"}
    wrong = {session.concept(e["concept_id"])["name"] for e in session.student_errors
             if not e["resolved"] and e.get("concept_id") in session.concepts}
    # an idea once explained stays mastered, unless the student holds a wrong idea about it right now
    mastered = [n for n in names if (n in previous or n in explained) and n not in wrong]
    weak = [n for n in names if n not in mastered]
    # missed, needed a hint, or never answered (session ended early): plant it again next time
    missed = [card_m["wrong_statement"] for card_m in card["misconceptions"]
              if session.plants[card_m["id"]]["status"] in ("revealed", "caught_after_hint", "planted")]
    plants = rep["planted"]
    profile["level"] = session.level
    profile["topics"][card["topic"]] = {
        "mastery": rep["mastery"],
        "concepts": names,
        "mastered": mastered,
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
        "plants_total": sum(p["outcome"] != "planted" for p in plants),  # unanswered tricks do not count
        "avg_quality": rep["avg_quality"],
        "deep_good": session.deep_good,
        "deep_asked": session.deep_asked,
    })
    return profile


def record_homework(profile: dict, rep: dict) -> dict:
    """Keep a short record of each homework question solved together (Homework mode)."""
    profile.setdefault("homework", []).append({
        "date": date.today().isoformat(), "question": rep["question"], "solved": rep["solved"],
        "steps_on_own": rep["steps_on_own"], "nudges": rep["nudges"], "graded": rep["answer_trusted"],
    })
    return profile


def add_quiz(profile: dict, pre: int | None, post: int, total: int) -> dict:
    """Attach the before/after quick-check scores to the latest session (learning gain = post - pre)."""
    profile["sessions"][-1].update({"quiz_pre": pre, "quiz_post": post, "quiz_total": total})
    return profile


def _pct(num: float, den: float) -> int | None:
    return round(100 * num / den) if den else None


def skills(profile: dict) -> dict:
    """Everything the progress dashboard shows, computed from the saved sessions (no LLM)."""
    sessions = profile["sessions"]
    measures = {
        "Explaining in your own words": lambda ss: _pct(sum(s["avg_quality"] for s in ss), 3 * len(ss)),
        "Spotting mistakes": lambda ss: _pct(sum(s["plants_caught"] for s in ss), sum(s["plants_total"] for s in ss)),
        "Thinking deeper (why / what if)": lambda ss: _pct(sum(s.get("deep_good", 0) for s in ss),
                                                            sum(s.get("deep_asked", 0) for s in ss)),
    }
    rows = []
    for name, measure in measures.items():
        value = measure(sessions)
        before, last = (measure(sessions[:-1]), measure(sessions[-1:])) if len(sessions) > 1 else (None, None)
        trend = last - before if None not in (before, last) else None  # latest session vs everything before it
        rows.append({"name": name, "value": value, "trend": trend})

    topics = [
        {"topic": topic, "mastery": t["mastery"], "mastered": t.get("mastered", []),
         "to_practice": t.get("weak_concepts", []), "retry": t.get("missed_plants", []),
         "wrong_ideas": t.get("open_errors", [])}
        for topic, t in profile["topics"].items()
    ]
    steps = []
    for t in topics:
        steps += [f'{t["topic"]}: you said "{w}". Check it against the page.' for w in t["wrong_ideas"]]
        if t["to_practice"]:
            steps.append(f'{t["topic"]}: teach these again: {", ".join(t["to_practice"])}.')
        steps += [f'{t["topic"]}: watch for this trick: "{m}"' for m in t["retry"]]
    quizzed = [s for s in sessions if s.get("quiz_pre") is not None and s.get("quiz_post") is not None]
    return {
        "sessions": len(sessions),
        "learning_gain": (round(sum(s["quiz_post"] - s["quiz_pre"] for s in quizzed) / len(quizzed), 2)
                          if quizzed else None),
        "quizzes": [{"date": s["date"], "topic": s["topic"], "pre": s["quiz_pre"], "post": s["quiz_post"],
                     "total": s["quiz_total"]} for s in quizzed],
        "level": profile["level"],
        "skills": rows,
        "topics": topics,
        "mastered_total": sum(len(t["mastered"]) for t in topics),
        "concepts_total": sum(len(p.get("concepts", [])) for p in profile["topics"].values()),
        "history": [{"date": s["date"], "topic": s["topic"], "mastery": s["mastery"]} for s in sessions],
        "next_steps": steps or ["Everything so far is solid. Try a new page, or a higher class."],
    }
