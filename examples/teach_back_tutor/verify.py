"""Fact-check the classmate's lines before the student sees them.

Follows Chain-of-Verification (Dhuliawala et al., Findings of ACL 2024), factored variant:
  1. plan:    list the factual claims in the draft line, each with a yes/no question checkable on the page
  2. execute: answer those questions from the page ONLY, without seeing the draft (so the draft cannot bias it)
  3. decide:  in Python. A claim is false only if the answer is "no" AND the quoted page evidence is really on
              the page. On a "plant" move exactly the planted mistake may be false; nothing else may be.
Plus a deterministic leak check: the classmate must not use lesson terms the student has not said yet
(it missed the class; see the knowledge state in tutor.py).
"""

from __future__ import annotations

import llm
from lesson import _words, is_grounded

PLAN_PROMPT = """A classmate said this line while being taught a school lesson:
\"\"\"{line}\"\"\"

List the factual claims about the lesson topic that the speaker ASSERTS AS TRUE right now.
Skip questions, feelings, greetings, and anything the speaker reports as an earlier mistake or a trick \
("I said X but..."). For each claim write one yes/no question that a textbook page could answer. \
Keep the claim's exact quantity words (only, all, none, never, always, most, some) in the question, e.g. \
"Do plants need ONLY water to make food?", not "Do plants need water?".
Return JSON: {{"claims": [{{"claim": "...", "question": "..."}}]}}. Use an empty list if there are none."""

ANSWER_PROMPT = """Answer each question using ONLY this textbook page. Do not use outside knowledge.

Page:
\"\"\"{page}\"\"\"

Questions:
{questions}

For each question answer "yes", "no" or "not_on_page", and copy the exact words from the page that decide it \
(empty if not_on_page).
Return JSON: {{"answers": [{{"n": 1, "answer": "yes", "evidence": "..."}}]}}"""

STOP = set("""about above after again against because before being below between could doing during every
first found friend going having their there these thing things those through under until using where which
while would should little really maybe means mean called makes making kind sort lesson class teach taught
explain tell think thing understand right wrong something someone people other another""".split())

# moves whose line legitimately contains a false statement (the planted mistake, said or quoted)
PLANT_MOVES = {"plant", "hint_plant", "reveal_plant"}


def _norm(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def key_terms(card: dict) -> set[str]:
    """Content words of the lesson that a classmate who missed it would not know."""
    topic = {_norm(w) for w in _words(card["topic"])}
    text = " ".join(f'{c["name"]} {c["explanation"]}' for c in card["concepts"])
    return {_norm(w) for w in _words(text) if len(w) >= 5 and w not in STOP and _norm(w) not in topic}


def leaked_terms(session, move: dict, line: str) -> list[str]:
    """Lesson terms in `line` that nobody has said in the conversation yet (wrap-up may name anything)."""
    if move["move"] == "wrap_up":
        return []
    allowed = " ".join(h["content"] for h in session.history)
    if move.get("mid"):  # the planted statement is scripted; only the reveal may also state the right idea
        m = session.misconception(move["mid"])
        allowed += " " + m["wrong_statement"]
        if move["move"] == "reveal_plant":
            allowed += f' {m["correction"]} {m["evidence"]}'
    if move["move"] == "probe_error":
        allowed += " " + move["error"]["claim"]
    if move["move"] == "opening":
        allowed += " " + " ".join(session.concept(c)["name"] for c in session.focus)
    known = {_norm(w) for w in _words(allowed)}
    found = []
    for w in _words(line):
        if _norm(w) in session.key_terms and _norm(w) not in known and w not in found:
            found.append(w)
    return found


def fact_check(session, move: dict, line: str) -> dict:
    """Steps 1-3 of CoVe for one line. Returns the verdicts and whether the line may be shown."""
    planned = llm.chat_json([{"role": "user", "content": PLAN_PROMPT.format(line=line)}], temperature=0)
    claims = [c for c in planned.get("claims", []) if isinstance(c, dict) and c.get("question")]
    verdicts = []
    if claims:
        questions = "\n".join(f"{i}. {c['question']}" for i, c in enumerate(claims, 1))
        answered = llm.chat_json([{"role": "user", "content": ANSWER_PROMPT.format(
            page=session.page_text, questions=questions)}], temperature=0)
        by_n = {int(a.get("n", 0)): a for a in answered.get("answers", []) if str(a.get("n", "")).isdigit()}
        page_words = set(_words(session.page_text))
        for i, c in enumerate(claims, 1):
            a = by_n.get(i, {})
            answer, evidence = str(a.get("answer", "not_on_page")).lower(), a.get("evidence", "")
            grounded = bool(evidence) and is_grounded(evidence, page_words)
            verdict = "true" if answer == "yes" and grounded else "false" if answer == "no" and grounded else "unverified"
            verdicts.append({"claim": c.get("claim", ""), "question": c["question"], "verdict": verdict,
                             "evidence": evidence if grounded else ""})
    return decide(session, move, line, verdicts)


def gives_away(line: str, correction: str, threshold: float = 0.6) -> bool:
    """True when most content words of the correction appear in the line (the hint states the answer)."""
    content = {_norm(w) for w in _words(correction) if len(w) >= 4 and w not in STOP}
    if not content:
        return False
    said = {_norm(w) for w in _words(line)}
    return len(content & said) / len(content) >= threshold


def _is_the_plant(claim: str, wrong_statement: str) -> bool:
    words = set(_words(wrong_statement))
    return is_grounded(claim, words, threshold=0.6) if words else False


def decide(session, move: dict, line: str, verdicts: list[dict]) -> dict:
    """Pure Python: which problems make this line unfit to show."""
    false = [v for v in verdicts if v["verdict"] == "false"]
    plant_present = None
    if move["move"] in PLANT_MOVES and move.get("mid"):
        wrong = session.misconception(move["mid"])["wrong_statement"]
        planted = [v for v in false if _is_the_plant(v["claim"], wrong)]
        if move["move"] == "plant":
            plant_present = bool(planted) or _is_the_plant(line, wrong)
        false = [v for v in false if v not in planted]
    leaks = leaked_terms(session, move, line)
    problems = [f'it states "{v["claim"]}", but the page says: "{v["evidence"]}"' for v in false]
    if leaks:
        problems.append(f"it uses words the student has not taught you yet: {', '.join(leaks)}")
    if move["move"] == "hint_plant" and gives_away(line, session.misconception(move["mid"])["correction"]):
        problems.append("it gives away the right idea; a hint must make the student think, not tell them")
    if plant_present is False:
        problems.append("it does not say the deliberate mistake it was told to say")
    return {"verdicts": verdicts, "false": false, "leaks": leaks, "plant_present": plant_present,
            "problems": problems, "ok": not problems}


def checked(session, move: dict, line: str, regenerate, use_llm: bool = True) -> tuple[str, dict]:
    """Fact-check a line; if it has problems, regenerate once with them as feedback and keep the better one.
    `use_llm=False` runs only the deterministic checks (for lines with no claims, like the opening)."""
    check = fact_check if use_llm else (lambda s, m, ln: decide(s, m, ln, []))
    first = check(session, move, line)
    caught = {"first_problems": first["problems"], "first_false": len(first["false"]), "first_leaks": first["leaks"]}
    if first["ok"]:
        return line, {**first, **caught, "regenerated": False}
    retry = regenerate("Your previous draft was not allowed because " + "; ".join(first["problems"])
                       + ". Write it again following every rule.")
    second = check(session, move, retry)
    if len(second["problems"]) <= len(first["problems"]):
        return retry, {**second, **caught, "regenerated": True}
    return line, {**first, **caught, "regenerated": False}
