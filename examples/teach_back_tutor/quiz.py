"""A short quiz taken before and after teaching, so each session has an objective learning gain.

Learning gain = post score - pre score, as in the Feynman Bot study (Rajesh & Khan, IEEE TALE 2024).
LLM-written questions can be wrong, so every question is checked the Chain-of-Verification way:
a separate call answers it from the page WITHOUT the answer key, and we keep only questions where
that independent answer matches the key and the key's evidence is really on the page.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import llm
from lesson import _words, is_grounded

QUIZ_DIR = Path(__file__).resolve().parent / "outputs" / "quizzes"

WRITE_PROMPT = """Write {n} multiple-choice questions that check UNDERSTANDING of this textbook lesson \
(not exact wording) for a school student. Use only facts on the page. Cover different key ideas. \
Each has 4 options with exactly one correct; make the wrong options believable student mistakes.

Key ideas:
{concepts}

Page:
\"\"\"{page}\"\"\"

Return JSON: {{"questions": [{{"q": "...", "options": ["...", "...", "...", "..."], "answer": 0, \
"concept_id": "c1", "evidence": "<exact words from the page that prove the answer>"}}]}}"""

SOLVE_PROMPT = """Answer each multiple-choice question using ONLY this page.

Page:
\"\"\"{page}\"\"\"

{questions}

Return JSON: {{"answers": [{{"n": 1, "choice": 0}}]}} where choice is the 0-based index of the option."""


def _as_text(questions: list[dict]) -> str:
    return "\n\n".join(f"{i}. {q['q']}\n" + "\n".join(f"   {j}) {o}" for j, o in enumerate(q["options"]))
                       for i, q in enumerate(questions, 1))


def build(card: dict, page_text: str, n: int = 3) -> dict:
    """{"questions": [...], "generated": k, "dropped": [{"q", "reason"}]} - cached per lesson card."""
    key = hashlib.sha256(json.dumps(card, sort_keys=True).encode()).hexdigest()[:16]
    cached = QUIZ_DIR / f"{key}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))

    concepts = "\n".join(f"- {c['id']}: {c['explanation']}" for c in card["concepts"])
    drafted = llm.chat_json([{"role": "user", "content": WRITE_PROMPT.format(
        n=n + 2, concepts=concepts, page=page_text)}], temperature=0.3).get("questions", [])
    page_words = set(_words(page_text))
    well_formed, dropped = [], []
    for q in drafted:
        ok = (isinstance(q, dict) and q.get("q") and isinstance(q.get("options"), list) and len(q["options"]) == 4
              and isinstance(q.get("answer"), int) and 0 <= q["answer"] < 4)
        if not ok:
            dropped.append({"q": str(q.get("q", q))[:120] if isinstance(q, dict) else str(q)[:120],
                            "reason": "malformed"})
        elif not is_grounded(q.get("evidence", ""), page_words):
            dropped.append({"q": q["q"], "reason": "answer's evidence is not on the page"})
        else:
            well_formed.append(q)

    kept = []
    if well_formed:
        solved = llm.chat_json([{"role": "user", "content": SOLVE_PROMPT.format(
            page=page_text, questions=_as_text(well_formed))}], temperature=0)
        choices = {int(a["n"]): a.get("choice") for a in solved.get("answers", []) if str(a.get("n", "")).isdigit()}
        for i, q in enumerate(well_formed, 1):
            if choices.get(i) == q["answer"]:
                kept.append(q)
            else:
                dropped.append({"q": q["q"], "reason": "an independent solver picked a different answer"})

    questions = [_shuffled(q) for q in kept[:n]]
    quiz = {"questions": questions, "generated": len(drafted), "dropped": dropped}
    if questions:
        QUIZ_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
    return quiz


def _shuffled(q: dict) -> dict:
    """LLMs favour putting the right answer first; shuffle the options deterministically."""
    order = list(range(4))
    random.Random(q["q"]).shuffle(order)
    return {**q, "options": [q["options"][i] for i in order], "answer": order.index(q["answer"])}


def score(quiz: dict, picks: list[int | None]) -> int:
    return sum(p == q["answer"] for p, q in zip(picks, quiz["questions"], strict=True))
