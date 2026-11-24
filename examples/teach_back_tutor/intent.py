"""Which mode does the student want? One small, fast LLM call, with keyword rules as the fallback.

The student adds a page and, optionally, says or types what they want ("help me with question 3",
"I have a test on this tomorrow"). The page itself is evidence too: a page of numbered questions is homework.

    INTENT_MODEL=openai/gpt-oss-20b   # optional; any small model on your LLM endpoint
"""

from __future__ import annotations

import os
import re

import llm

INTENT_PROMPT = """A school student opened a study app with a textbook page. Pick the mode that fits.
- "teach": the student explains the lesson to a classmate, to revise or understand it (lesson pages, \
"explain", "revise", "test tomorrow", "I don't understand this chapter").
- "homework": the student solves exercise or practice questions from the page step by step with hints \
(exercise pages, worksheets, "solve", "homework", "question 3", "answer this").
The student said: "{request}"
Start of the page:
\"\"\"{excerpt}\"\"\"
Return JSON: {{"mode": "teach" | "homework", "question": "<the question number or words the student named, or ''>", \
"reason": "<at most 12 words, addressed to the student>"}}"""

HOMEWORK_WORDS = r"\b(homework|home work|hw|solve|exercise|worksheet|question|answer|sum|problem|calculate|find the|" \
                 r"sawal|sawaal|hal karo|ghar ka kaam)\b"
NUMBERED = r"(?m)^\s*(?:Q\.?\s*)?\d{1,2}[.)]\s+\S"  # "1. The road is..." / "Q2) ..."


def rules(request: str, page_text: str) -> dict:
    """No LLM: what the student said wins; otherwise a page with several numbered questions is homework."""
    said = request.lower()
    if re.search(HOMEWORK_WORDS, said):
        return {"mode": "homework", "question": _question_number(said), "reason": "You asked for help with questions."}
    if said.strip():
        return {"mode": "teach", "question": "", "reason": "You want to understand the lesson."}
    numbered = len(re.findall(NUMBERED, page_text))
    if numbered >= 3 and "?" in page_text or numbered >= 4:
        return {"mode": "homework", "question": "", "reason": "This page is mostly practice questions."}
    return {"mode": "teach", "question": "", "reason": "This looks like a lesson page."}


def _question_number(text: str) -> str:
    m = re.search(r"\b(?:q|question|sawal|no\.?|number)\s*\.?\s*(\d{1,2})\b", text)
    return m.group(1) if m else ""


def classify(request: str, page_text: str) -> dict:
    """{"mode", "question", "reason", "by"}; "by" says whether the small LLM or the rules decided."""
    model = os.environ.get("INTENT_MODEL", "openai/gpt-oss-20b")
    try:
        got = llm.chat_json([{"role": "user", "content": INTENT_PROMPT.format(
            request=request.strip() or "(nothing, they just added the page)", excerpt=page_text[:1500])}],
            temperature=0, model=model, attempts=1)  # no waiting: the rules below are a fine fallback
        if got.get("mode") in ("teach", "homework"):
            return {"mode": got["mode"], "question": str(got.get("question", "") or ""),
                    "reason": str(got.get("reason", ""))[:120], "by": model}
    except Exception:  # noqa: BLE001 - any failure (no such model, rate limit, bad JSON) falls back to rules
        pass
    return {**rules(request, page_text), "by": "rules"}


def pick_question(questions: list[str], hint: str) -> int:
    """Index of the question the student named ("3", "question 3" or some of its words); 0 if unclear."""
    if not questions or not hint:
        return 0
    n = re.fullmatch(r"\s*(?:q(?:uestion)?\.?\s*|no\.?\s*)?(\d{1,2})\s*", hint, re.IGNORECASE)
    if n and 1 <= int(n.group(1)) <= len(questions):
        return int(n.group(1)) - 1
    words = set(re.findall(r"\w{3,}", hint.lower()))
    overlap = [len(words & set(re.findall(r"\w{3,}", q.lower()))) for q in questions]
    return overlap.index(max(overlap)) if max(overlap) else 0
