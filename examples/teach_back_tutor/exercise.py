"""Homework mode: the classmate is stuck on an exercise from the page, and the student helps solve it.

The student still does the explaining (learning by teaching). When they get stuck, the classmate nudges:
first a question about what to find next, then a half-step guess, and only then it reveals the step.

Exercise answers are not printed on the page, so the reference solution comes from the LLM. It is checked
with self-consistency (Wang et al., ICLR 2023): solve the exercise 3 times independently and trust the answer
only if a majority agree. Otherwise the classmate says it is not sure either, and nothing is graded.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import llm
import tutor
from lesson import _words, is_grounded
from verify import gives_away

SOLVES = 3
MAX_TURNS = 10

FIND_PROMPT = """This is the text of a school textbook page. List the exercise or practice questions on it, \
copied word for word (without their numbers). Ignore headings and explanations.
Page:
\"\"\"{page}\"\"\"
Return JSON: {{"questions": ["...", "..."]}}. Use an empty list if there are none."""

SOLVE_PROMPT = """Solve this school exercise for a {band} student, using the textbook page for context.
Page:
\"\"\"{page}\"\"\"
Exercise: {question}
Return JSON: {{"steps": ["2 to 5 short steps, each one idea"], "final_answer": "short final answer"}}"""

AGREE_PROMPT = """These are {n} answers to the same school exercise: "{question}"
{answers}
Group the answers that reach the same conclusion, even if they are worded differently or give different \
amounts of explanation. Only separate answers that disagree.
Return JSON: {{"groups": [[1, 2], [3]]}}"""

EVAL_PROMPT = """A student is helping a classmate solve this exercise: "{question}"
Reference solution steps:
{steps}
Reference final answer: {answer}

Recent conversation:
{recent}

Student's latest message:
\"\"\"{message}\"\"\"

Return JSON with these keys:
- "steps": list of step numbers the latest message explains correctly in the student's own words. Agreeing \
with or repeating the classmate's words does not count.
- "wrong": a wrong idea or wrong calculation the student states, in one short sentence, or "" if none.
- "final_answer": the final answer the student gives, or "" if none yet.
- "final_correct": true only if that final answer matches the reference.
- "quality": 0-3 (0 no attempt, 1 recall without reasoning, 2 clear reasoning, 3 clear and adds a reason or check)."""

PERSONA = """You are {name}, {persona}
You are stuck on this homework question and your friend (the student) is helping you solve it:
"{question}"
You do NOT know the answer. Never state the final answer or a solution step your friend has not given, \
unless the director's instruction says REVEAL.
Rules: warm and encouraging; never say "wrong"; ask at most ONE question; 1-3 short spoken sentences; simple English.
What your friend has taught you so far:
{notebook}"""


def find_questions(page_text: str) -> list[str]:
    """Exercise questions on the page, kept only if they really appear on it (OCR text, not invented)."""
    found = llm.chat_json([{"role": "user", "content": FIND_PROMPT.format(page=page_text)}], temperature=0)
    page_words = set(_words(page_text))
    return [q for q in found.get("questions", []) if isinstance(q, str) and is_grounded(q, page_words)]


def _norm(answer: str) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", answer.lower()).strip()


def solve(question: str, page_text: str, band: str) -> dict:
    """Self-consistency: SOLVES independent solutions; trust the majority answer if there is one."""
    label = tutor.BANDS[band]["label"]
    prompt = SOLVE_PROMPT.format(band=label, page=page_text, question=question)

    def one(_):
        return llm.chat_json([{"role": "user", "content": prompt}], temperature=0.8)

    with ThreadPoolExecutor(max_workers=SOLVES) as pool:
        tries = [t for t in pool.map(one, range(SOLVES)) if t.get("final_answer") and t.get("steps")]
    if not tries:
        return {"trusted": False, "agreement": f"0/{SOLVES}", "steps": [], "final_answer": "", "answers": []}
    answers = [str(t["final_answer"]) for t in tries]
    numbers = [tuple(re.findall(r"\d+(?:\.\d+)?", a)) for a in answers]
    if len({_norm(a) for a in answers}) == 1:
        groups = [list(range(1, len(answers) + 1))]
    elif all(numbers):  # numeric answers: same numbers = same answer, decided in Python
        by_value: dict[tuple, list[int]] = {}
        for i, key in enumerate(numbers, 1):
            by_value.setdefault(key, []).append(i)
        groups = list(by_value.values())
    else:
        listed = "\n".join(f"{i}. {a}" for i, a in enumerate(answers, 1))
        groups = llm.chat_json([{"role": "user", "content": AGREE_PROMPT.format(
            n=len(answers), question=question, answers=listed)}], temperature=0).get("groups", [])
        groups = [[i for i in g if isinstance(i, int) and 1 <= i <= len(answers)] for g in groups if isinstance(g, list)]
    best = max(groups, key=len) if groups else [1]
    pick = tries[best[0] - 1]
    return {"trusted": len(best) * 2 > SOLVES, "agreement": f"{len(best)}/{SOLVES}", "answers": answers,
            "steps": [str(s) for s in pick["steps"]][:5], "final_answer": str(pick["final_answer"])}


@dataclass
class Homework:
    question: str
    ref: dict  # from solve()
    band: str = "middle"
    page_text: str = ""
    steps: list = field(default_factory=list)  # per step: not_yet / explained / after_nudge / revealed / skipped
    nudges: list = field(default_factory=list)  # per reference step: nudges given so far
    final: str = ""  # the student's final answer
    final_correct: bool = False
    errors: list = field(default_factory=list)
    probed: set = field(default_factory=set)
    asked: set = field(default_factory=set)  # steps already asked about after a correct final answer
    quality: list = field(default_factory=list)
    stuck: int = 0
    turn: int = 0
    history: list = field(default_factory=list)
    learned: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    done: bool = False
    level: int = 1

    def __post_init__(self):
        self.steps = self.steps or ["not_yet"] * len(self.ref["steps"])
        self.nudges = self.nudges or [0] * len(self.ref["steps"])

    @property
    def cfg(self) -> dict:
        return tutor.BANDS[self.band]

    def next_step(self) -> int | None:
        return next((i for i, s in enumerate(self.steps) if s == "not_yet"), None)


def evaluate(hw: Homework, message: str) -> dict:
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(hw.ref["steps"], 1))
    recent = "\n".join(f"{'Classmate' if h['role'] == 'assistant' else 'Student'}: {h['content']}"
                       for h in hw.history[-4:]) or "(start)"
    prompt = EVAL_PROMPT.format(question=hw.question, steps=steps, answer=hw.ref["final_answer"],
                                recent=recent, message=message)
    try:
        return llm.chat_json([{"role": "user", "content": prompt}])
    except ValueError:
        return {"steps": [], "wrong": "", "final_answer": "", "final_correct": False, "quality": 1}


def update(hw: Homework, ev: dict) -> dict:
    """Python decides what the evaluation means for the session."""
    new = []
    for n in ev.get("steps", []):
        i = int(n) - 1 if str(n).isdigit() else -1
        if 0 <= i < len(hw.steps) and hw.steps[i] == "not_yet":
            hw.steps[i] = "after_nudge" if hw.nudges[i] else "explained"
            new.append(i)
    if ev.get("final_answer"):
        hw.final = str(ev["final_answer"])
        hw.final_correct = bool(ev.get("final_correct")) and hw.ref["trusted"]
    wrong = str(ev.get("wrong", "")).strip()
    if wrong and hw.ref["trusted"]:
        hw.errors.append(wrong)
    hw.quality.append(int(ev.get("quality", 0) or 0))
    hw.stuck = 0 if new or ev.get("final_answer") else hw.stuck + 1
    for i in new:
        hw.learned.append({"turn": hw.turn, "text": hw.ref["steps"][i], "how": "you explained"})
    return {"new_steps": new, "wrong": wrong}


def choose_move(hw: Homework, changes: dict) -> dict:
    if hw.turn >= MAX_TURNS:
        return {"move": "wrap_up"}
    if not hw.ref["trusted"]:  # the answer key is not reliable: follow along, grade nothing
        return {"move": "check_with_teacher"} if hw.final else {"move": "ask_next"}
    if changes["wrong"] and changes["wrong"] not in hw.probed:
        hw.probed.add(changes["wrong"])
        return {"move": "probe_error", "wrong": changes["wrong"]}
    k = hw.next_step()
    if hw.final_correct:  # right answer: ask once about each skipped step (Feynman: explain how), then finish
        while k is not None and k in hw.asked:
            hw.steps[k] = "skipped"
            k = hw.next_step()
        if k is None:
            return {"move": "wrap_up"}
        hw.asked.add(k)
        return {"move": "ask_why", "step": k}
    if k is None:
        return {"move": "ask_final"}
    if hw.stuck:
        level = hw.nudges[k]
        hw.nudges[k] += 1
        if level >= 2:
            hw.steps[k] = "revealed"
            hw.learned.append({"turn": hw.turn, "text": hw.ref["steps"][k], "how": "I worked it out"})
            return {"move": "reveal_step", "step": k}
        return {"move": "nudge_goal" if level == 0 else "nudge_guess", "step": k}
    return {"move": "ask_next"}


def _instruction(hw: Homework, move: dict) -> str:
    mv = move["move"]
    step = hw.ref["steps"][move["step"]] if "step" in move else ""
    return {
        "opening": "Say hi, read out the homework question you are stuck on, and ask your friend to help you solve it.",
        "ask_next": "React to what they said, then ask what you should do next (no hints).",
        "ask_final": "All the steps are there. Ask them what the final answer is, then.",
        "nudge_goal": f'They seem stuck. Without giving the step, ask a question about WHAT you need to find or '
                      f'use next. (Secret next step, never say it: "{step}")',
        "nudge_guess": f'Still stuck. Offer a half-finished guess that points the right way, then ask them to finish '
                       f'it. Do not complete it. (Secret next step: "{step}")',
        "reveal_step": f'REVEAL: say you think you figured out this step yourself: "{step}". Then ask them what '
                       f'comes after it.',
        "probe_error": f'They said something that does not look right: "{move.get("wrong", "")}". Do not correct '
                       "them. Ask a puzzled question that makes them check it.",
        "ask_why": f'They got the right answer but skipped a step. Ask them to explain how: "{step}" (secret, '
                   "do not say it, ask about it in everyday words).",
        "check_with_teacher": "Thank them, say you are not sure the answer is right either, and suggest checking "
                              "it with the teacher tomorrow. No question.",
        "wrap_up": "Wrap up: thank them warmly for helping, say the final answer you two found, and name one step "
                   "they explained really well. No question.",
    }[mv]


def respond(hw: Homework, move: dict, feedback: str = "") -> str:
    notebook = "\n".join(f"- {n['text']}" for n in hw.learned) or "- (nothing yet)"
    system = PERSONA.format(name=hw.cfg["name"], persona=hw.cfg["persona"], question=hw.question, notebook=notebook)
    note = f"[director note, not from the student] Instruction: {_instruction(hw, move)}"
    if feedback:
        note += "\n" + feedback
    return llm.chat([{"role": "system", "content": system}, *hw.history[-8:], {"role": "user", "content": note}])


def check_line(hw: Homework, move: dict, line: str) -> list[str]:
    """Deterministic guard: the classmate may not give away the answer or the next step before it should."""
    problems = []
    if move["move"] not in ("reveal_step", "wrap_up", "check_with_teacher") and hw.ref["final_answer"]:
        answer = _norm(hw.ref["final_answer"])
        numbers = re.findall(r"\d+(?:\.\d+)?", answer)
        said = _norm(line)
        gave_number = numbers and all(n in said.split() for n in numbers) and not hw.final
        if gave_number or (not numbers and gives_away(line, hw.ref["final_answer"]) and not hw.final):
            problems.append("it gives away the final answer")
    if move["move"] in ("nudge_goal", "nudge_guess", "ask_why", "ask_next") and "step" in move:
        if gives_away(line, hw.ref["steps"][move["step"]], threshold=0.7):
            problems.append("it states the next step instead of nudging")
    return problems


def _speak(hw: Homework, move: dict) -> tuple[str, dict]:
    line = respond(hw, move)
    problems = check_line(hw, move, line)
    check = {"move": move["move"], "first_problems": problems, "regenerated": False, "ok": not problems}
    if problems:
        retry = respond(hw, move, "Your previous draft was not allowed because " + "; ".join(problems)
                        + ". Write it again following every rule.")
        still = check_line(hw, move, retry)
        if len(still) <= len(problems):
            line, check = retry, {**check, "regenerated": True, "ok": not still}
    hw.checks.append(check)
    return line, check


def start(hw: Homework) -> tuple[str, dict]:
    line, check = _speak(hw, {"move": "opening"})
    hw.history.append({"role": "assistant", "content": line})
    return line, check


def step(hw: Homework, message: str) -> tuple[str, dict]:
    hw.turn += 1
    ev = evaluate(hw, message)
    hw.history.append({"role": "user", "content": message})
    changes = update(hw, ev)
    move = choose_move(hw, changes)
    line, check = _speak(hw, move)
    hw.history.append({"role": "assistant", "content": line})
    if move["move"] in ("wrap_up", "check_with_teacher"):
        hw.done = True
    return line, {"eval": ev, "move": move["move"], "level": hw.level, "check": check}


def report(hw: Homework) -> dict:
    own = sum(s == "explained" for s in hw.steps)
    return {
        "question": hw.question,
        "answer_trusted": hw.ref["trusted"],
        "agreement": hw.ref["agreement"],
        "reference_answer": hw.ref["final_answer"],
        "student_answer": hw.final,
        "solved": hw.final_correct,
        "steps": [{"step": s, "status": st} for s, st in zip(hw.ref["steps"], hw.steps, strict=True)],
        "steps_on_own": f"{own}/{len(hw.steps)}",
        "nudges": sum(hw.nudges),
        "errors": hw.errors,
        "avg_quality": round(sum(hw.quality) / len(hw.quality), 2) if hw.quality else 0,
        "turns": hw.turn,
        "rewritten": sum(c["regenerated"] for c in hw.checks),
    }
