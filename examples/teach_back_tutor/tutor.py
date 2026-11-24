"""Feynman teach-back engine.

Each turn:
  1. evaluate()  - the LLM scores the student's message against the lesson card (JSON, low temperature)
  2. update()    - plain Python updates concept mastery, misconception state and the adaptive level
  3. choose_move() - plain Python picks what the buddy does next
  4. respond()   - the LLM writes the buddy's line in persona, following that move

The LLM judges and talks; the code decides. That keeps the pedagogy testable.

    python tutor.py --band middle        # chat by typing
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field

import llm
import verify

BANDS = {
    "primary": {
        "label": "Class 1-5",
        "name": "Chintu",
        "persona": "a playful, bubbly classmate. Very short simple sentences, lots of excitement, "
        "little games like 'ooh, is it like...?'. Celebrate every small thing the student says.",
        "voice_style": "children's stories",
        "max_plants": 1,
        "plant_difficulty": ["easy"],
        "levels": (1, 2),
        "deep_checks": 0,
    },
    "middle": {
        "label": "Class 6-8",
        "name": "Riya",
        "persona": "a curious explorer of a classmate. Friendly, asks 'what if' and connects ideas "
        "to everyday life (kitchen, cricket, the school garden).",
        "voice_style": "happy",
        "max_plants": 2,
        "plant_difficulty": ["easy", "medium"],
        "levels": (1, 3),
        "deep_checks": 1,
    },
    "secondary": {
        "label": "Class 9-12",
        "name": "Arjun",
        "persona": "a sharp, friendly debater of a classmate. Pushes back, asks for reasons and "
        "evidence ('but how do you know?'), plays devil's advocate, always respectful.",
        "voice_style": "educational lecture",
        "max_plants": 3,
        "plant_difficulty": ["easy", "medium", "hard"],
        "levels": (2, 3),
        "deep_checks": 2,
    },
}

LEVEL_QUESTIONS = {
    1: "simple 'what' questions about one idea at a time",
    2: "'how' and 'why' questions that ask the student to connect two ideas",
    3: "'what if' questions, applying the idea to a new situation, or a gentle challenge to their reasoning",
}
DIFFICULTY_FOR_LEVEL = {1: "easy", 2: "medium", 3: "hard"}
MAX_TURNS = 12
UNTESTED_PLANT_TURN = 4  # by this turn, plant a mistake even if nothing is fully explained yet
MAX_CLARIFY = 2  # asking about the same vague idea more often than this just loops
STATUS_RANK = {"not_covered": 0, "partial": 1, "explained": 2}


@dataclass
class Session:
    card: dict
    band: str = "middle"
    level: int = 0
    turn: int = 0
    concepts: dict = field(default_factory=dict)
    plants: dict = field(default_factory=dict)
    pending_plant: str | None = None
    student_errors: list = field(default_factory=list)
    quality: list = field(default_factory=list)
    level_trace: list = field(default_factory=list)
    deep_asked: int = 0
    deep_good: int = 0
    history: list = field(default_factory=list)
    last_move: str = ""
    done: bool = False
    memory: dict = field(default_factory=dict)  # from memory.for_session(); empty for a new student
    focus: set = field(default_factory=set)  # concept ids the student was weak on last time
    retest: set = field(default_factory=set)  # misconception ids they missed last time
    page_text: str = ""  # the OCR'd page(s); the fact-checker answers from this alone
    fact_check: bool = True
    learned: list = field(default_factory=list)  # the classmate's notebook: only what the student taught it
    checks: list = field(default_factory=list)  # one fact-check summary per classmate line
    key_terms: set = field(default_factory=set)
    opening_check: dict | None = None
    clarified: dict = field(default_factory=dict)  # concept id -> times the classmate asked to clarify it

    def __post_init__(self):
        cfg = BANDS[self.band]
        self.key_terms = verify.key_terms(self.card)
        lo, hi = cfg["levels"]
        remembered = self.memory.get("level")
        self.level = self.level or (min(hi, max(lo, remembered)) if remembered else lo)
        weak = set(self.memory.get("weak_concepts", []))
        self.focus = {c["id"] for c in self.card["concepts"] if c["name"] in weak}
        missed = set(self.memory.get("retest_plants", []))
        self.retest = {m["id"] for m in self.card["misconceptions"] if m["wrong_statement"] in missed}
        for c in self.card["concepts"]:
            self.concepts.setdefault(c["id"], {"status": "not_covered", "said": ""})
        for m in self.card["misconceptions"]:
            self.plants.setdefault(m["id"], {"status": "unused", "hints": 0})

    @property
    def cfg(self) -> dict:
        return BANDS[self.band]

    def concept(self, cid: str) -> dict:
        return next(c for c in self.card["concepts"] if c["id"] == cid)

    def misconception(self, mid: str) -> dict:
        return next(m for m in self.card["misconceptions"] if m["id"] == mid)


# ---------------------------------------------------------------- evaluate

EVAL_PROMPT = """You are grading a school student who is explaining a textbook lesson to a classmate.

The student's words come from speech recognition and may contain mis-heard words. Judge the \
MEANING; do not treat an odd or mis-heard word as a mistake if the intended idea is clear.

Key concepts (the reference answers):
{concepts}

{plant_block}
Recent conversation:
{recent}

The buddy's last message (what the student is replying to):
\"\"\"{buddy_last}\"\"\"

Student's latest message:
\"\"\"{message}\"\"\"

Credit rules (be strict; this is the whole point of the exercise):
- "explained" ONLY if the student states the idea themselves, correctly, in their own words.
- If the student merely agrees ("yes", "you're right") or repeats words/ideas the buddy just put in \
its question, that is at most "partial".
- A vague gesture at the idea ("the sun does something") is "partial".
- "explained" needs EVERY part of the reference answer. If a part is missing (one of several ingredients, \
or what is produced), it is "partial". An analogy alone ("it's like an oven") does not supply missing parts.

Return JSON with exactly these keys:
- "concepts": list of {{"id", "status", "quote"}} for every concept the latest message touches. \
"status" is "explained", "partial" or "not_covered". "quote" is the student's own words you based it on.
- "plant_response": {plant_choices}
- "student_errors": list of {{"concept_id", "claim", "correction"}} for genuinely wrong ideas the \
student stated (not mis-heard words). Do NOT list agreement with the buddy's deliberate mistake here; \
that is captured by "plant_response". Do not repeat an error already in the open list below. Empty list if none.
- "resolved_errors": list of numbers from the open list below that the student's latest message \
now clearly corrects in their own words. Empty list if none.

Open errors the student made earlier:
{open_errors}
- "quality": 0-3. 0 = off-topic or no attempt, 1 = recalls words without understanding, \
2 = clear explanation in own words, 3 = clear AND adds a reason, example or connection.
- "short_feedback": one sentence on what they did well or what is missing."""


def _recent(session: Session, n: int = 4) -> str:
    lines = [f"{'Buddy' if h['role'] == 'assistant' else 'Student'}: {h['content']}" for h in session.history[-n:]]
    return "\n".join(lines) or "(start of conversation)"


def evaluate(session: Session, message: str) -> dict:
    concepts = "\n".join(f"- {c['id']}: {c['explanation']}" for c in session.card["concepts"])
    if session.pending_plant:
        m = session.misconception(session.pending_plant)
        plant_block = (
            f"In its last message the buddy deliberately said something WRONG to test the student:\n"
            f'  wrong: "{m["wrong_statement"]}"\n  correct: "{m["correction"]}"\n'
        )
        plant_choices = (
            '"caught" if the student disagreed with or corrected the wrong statement, '
            '"missed" if they agreed or ignored it'
        )
    else:
        plant_block = ""
        plant_choices = '"none"'
    buddy_last = next((h["content"] for h in reversed(session.history) if h["role"] == "assistant"), "")
    open_errors = "\n".join(
        f"{i}. {e['claim']}" for i, e in enumerate(session.student_errors) if not e["resolved"]
    ) or "(none)"
    prompt = EVAL_PROMPT.format(
        open_errors=open_errors,
        concepts=concepts,
        plant_block=plant_block,
        recent=_recent(session),
        buddy_last=buddy_last,
        message=message,
        plant_choices=plant_choices,
    )
    try:
        return llm.chat_json([{"role": "user", "content": prompt}])
    except ValueError:  # unparseable grading: keep the conversation going, score nothing this turn
        return {"concepts": [], "plant_response": "missed" if session.pending_plant else "none",
                "student_errors": [], "resolved_errors": [], "quality": 1, "short_feedback": "",
                "grading_failed": True}


# ---------------------------------------------------------------- update + policy


def update(session: Session, ev: dict) -> dict:
    """Apply an evaluation to the session. Returns what changed, for the policy and the responder."""
    changes = {"newly_explained": [], "caught": None, "missed": None, "errors": []}
    for item in ev.get("concepts", []):
        cid, status = item.get("id"), item.get("status")
        if cid not in session.concepts or status not in STATUS_RANK:
            continue
        cur = session.concepts[cid]
        if STATUS_RANK[status] > STATUS_RANK[cur["status"]]:  # mastery only goes up within a session
            cur["status"] = status
            cur["said"] = item.get("quote", "")
            if cur["said"]:
                session.learned.append({"turn": session.turn, "text": cur["said"], "how": "you explained"})
            if status == "explained":
                changes["newly_explained"].append(cid)

    if session.pending_plant:
        p = session.plants[session.pending_plant]
        if ev.get("plant_response") == "caught":
            p["status"] = "caught" if p["hints"] == 0 else "caught_after_hint"
            changes["caught"] = session.pending_plant
            session.learned.append({"turn": session.turn, "how": "you corrected my mistake",
                                    "text": session.misconception(session.pending_plant)["correction"]})
            session.pending_plant = None
        else:
            p["hints"] += 1
            changes["missed"] = session.pending_plant

    # an earlier error is resolved when the student corrects it, or explains that concept properly
    resolved_idx = {int(i) for i in ev.get("resolved_errors", []) if str(i).isdigit()}
    for i, err in enumerate(session.student_errors):
        if not err["resolved"] and (i in resolved_idx or err.get("concept_id") in changes["newly_explained"]):
            err["resolved"] = True

    open_concepts = {e.get("concept_id") for e in session.student_errors if not e["resolved"]}
    for err in ev.get("student_errors", []):
        cid = err.get("concept_id")
        if err.get("claim") and cid not in open_concepts:  # one open error per concept
            session.student_errors.append({**err, "turn": session.turn, "probed": False, "resolved": False})
            open_concepts.add(cid)
            changes["errors"].append(err)

    q = int(ev.get("quality", 0) or 0)
    session.quality.append(q)
    if session.last_move == "deepen" and q >= 2:
        session.deep_good += 1

    # adaptive level: one good answer is not enough to climb; a catch is strong evidence on its own
    lo, hi = session.cfg["levels"]
    last_two = session.quality[-2:]
    steady = len(last_two) == 2 and min(last_two) >= 2
    struggling = len(last_two) == 2 and max(last_two) <= 1
    if (changes["caught"] and q >= 2) or (steady and changes["newly_explained"]):
        session.level = min(hi, session.level + 1)
    elif q <= 0 or struggling or (changes["missed"] and session.plants[changes["missed"]]["hints"] >= 2):
        session.level = max(lo, session.level - 1)
    session.level_trace.append(session.level)
    return changes


def _pick_plant(session: Session) -> str | None:
    """A misconception about a concept the student has already explained, at the current level's difficulty.

    A student who explains nothing would otherwise never be tested, so after a few turns the first
    plant may target any concept: agreeing with it shows which wrong idea they hold.
    """
    used = sum(p["status"] != "unused" for p in session.plants.values())
    if used >= session.cfg["max_plants"]:
        return None
    allowed = session.cfg["plant_difficulty"]
    want = DIFFICULTY_FOR_LEVEL[session.level]
    any_concept = used == 0 and session.turn >= UNTESTED_PLANT_TURN

    def explained(m):
        return session.concepts.get(m["concept_id"], {}).get("status") == "explained"

    # where the student already holds a wrong idea, probe_error is at work; a plant there tests nothing
    holds_error = {e.get("concept_id") for e in session.student_errors if not e["resolved"]}
    # a mistake missed last session is re-planted first, to check that the correction stuck
    candidates = [
        m for m in session.card["misconceptions"]
        if session.plants[m["id"]]["status"] == "unused"
        and m["concept_id"] not in holds_error
        and m.get("difficulty", "easy") in allowed
        and (explained(m) or any_concept or m["id"] in session.retest)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda m: (m["id"] not in session.retest, not explained(m), m.get("difficulty") != want,
                                   allowed.index(m.get("difficulty", "easy"))))
    return candidates[0]["id"]


def choose_move(session: Session) -> dict:
    """Decide the buddy's next move. Returns {"move", ...target info}."""
    if session.pending_plant:
        hints = session.plants[session.pending_plant]["hints"]
        if hints == 1:
            return {"move": "hint_plant", "mid": session.pending_plant}
        session.plants[session.pending_plant]["status"] = "revealed"
        mid, session.pending_plant = session.pending_plant, None
        session.learned.append({"turn": session.turn, "how": "I revealed my trick",
                                "text": session.misconception(mid)["correction"]})
        return {"move": "reveal_plant", "mid": mid}

    unprobed = [e for e in session.student_errors if not e["probed"] and not e["resolved"]]
    if unprobed:
        unprobed[0]["probed"] = True
        return {"move": "probe_error", "error": unprobed[0]}

    statuses = {cid: c["status"] for cid, c in session.concepts.items()}
    all_explained = all(s == "explained" for s in statuses.values())
    plants_left = _pick_plant(session) is not None
    if session.turn >= MAX_TURNS:
        return {"move": "wrap_up"}
    if all_explained and not plants_left:
        if session.deep_asked < session.cfg["deep_checks"]:
            # covering the page isn't enough: push past recall to why / what-if before finishing
            explained = list(statuses)
            session.deep_asked += 1
            return {"move": "deepen", "cid": explained[session.turn % len(explained)]}
        return {"move": "wrap_up"}

    if session.turn >= 2 and session.last_move not in ("plant", "hint_plant", "reveal_plant"):
        mid = _pick_plant(session)
        untested = all(p["status"] == "unused" for p in session.plants.values())
        if mid and (session.turn % 2 == 0 or session.level >= 3 or (untested and session.turn >= UNTESTED_PLANT_TURN)):
            session.plants[mid]["status"] = "planted"
            session.pending_plant = mid
            return {"move": "plant", "mid": mid}

    # concepts the student was weak on last session come first; clarify one idea at most twice, then move on
    partial = sorted((cid for cid, s in statuses.items() if s == "partial" and session.clarified.get(cid, 0) < MAX_CLARIFY),
                     key=lambda c: c not in session.focus)
    if partial:
        session.clarified[partial[0]] = session.clarified.get(partial[0], 0) + 1
        return {"move": "clarify", "cid": partial[0]}
    uncovered = sorted((cid for cid, s in statuses.items() if s == "not_covered"), key=lambda c: c not in session.focus)
    if uncovered:
        return {"move": "probe_gap", "cid": uncovered[0]}
    explained = [cid for cid, s in statuses.items() if s == "explained"]
    session.deep_asked += 1
    return {"move": "deepen", "cid": explained[session.turn % len(explained)]}


# ---------------------------------------------------------------- respond

PERSONA_PROMPT = """You are {name}, {persona}
You MISSED the class on "{topic}" and your friend (the student) is teaching it to you.

Rules:
- You are a learner, not a teacher. Never explain the lesson yourself unless the instruction says REVEAL.
- Never say "wrong" or "incorrect". Be warm, supportive and non-judgemental; make the student feel smart.
- First react briefly to what the student just said, then follow the instruction.
- Ask at most ONE question. Keep it to 1-3 short sentences, spoken style, no lists, no emojis.
- Pitch questions as: {level_style}.
- Reply in simple English.

Everything you know about this topic is what your friend has taught you so far:
{notebook}
You know NOTHING else about it. Do not use facts or lesson words that are not in this list or in the \
director's instruction; ask about them in everyday words instead."""


def _instruction(session: Session, move: dict, changes: dict) -> str:
    notes = []
    if changes.get("caught"):
        m = session.misconception(changes["caught"])
        notes.append(f'The student just caught your deliberate mistake ("{m["wrong_statement"]}"). '
                     "Thank them warmly for correcting you, like a friend would.")
    if changes.get("newly_explained"):
        said = "; ".join(f'"{session.concepts[c]["said"]}"' for c in changes["newly_explained"])
        notes.append(f"They explained this well: {said}. Show you get it now.")

    mv = move["move"]
    if mv == "opening":
        mem, topic = session.memory, session.card["topic"]
        if mem.get("same_topic"):
            weak = [session.concept(c)["name"] for c in session.focus]
            task = (f'Say hi. You remember they taught you "{topic}" before, but admit you got a bit mixed up'
                    + (f" about {', '.join(weak)}" if weak else "") + ". Ask them to teach it to you again.")
        elif mem.get("last_topic"):
            task = (f'Say hi and that you liked it when they taught you "{mem["last_topic"]}" last time. Admit you '
                    f'missed today\'s class on "{topic}" and ask them to explain what it was about.')
        else:
            task = "Say hi, admit you missed the class, and ask them to explain what the lesson was about."
    elif mv == "probe_gap":
        c = session.concept(move["cid"])
        task = (f'Ask a curious, naive question that leads them to explain this idea WITHOUT stating it: '
                f'"{c["explanation"]}" (secret, never say it). Do not use any term from the secret that the '
                "student has not said themselves; ask in everyday words, as someone who missed the class would.")
    elif mv == "clarify":
        said = session.concepts[move["cid"]]["said"]
        task = (f'They were vague when they said "{said}". Say you are a bit confused by that part and ask them '
                "to explain it more clearly or with an example. Use only words they used, not textbook terms.")
    elif mv == "deepen":
        said = session.concepts[move["cid"]]["said"]
        task = (f'They explained this well: "{said}". Ask a harder follow-up about it, at the level described '
                "above, in everyday words.")
    elif mv == "plant":
        m = session.misconception(move["mid"])
        task = (f'Confidently say this as if you believe it, then ask if you got it right: "{m["wrong_statement"]}". '
                "It is a deliberate mistake to see if they catch it. Do not hint that it is wrong.")
    elif mv == "hint_plant":
        m = session.misconception(move["mid"])
        task = (f'They did not catch your mistake ("{m["wrong_statement"]}"). Do not reveal it and do NOT state the '
                "right idea. Sound a bit unsure about what you said and ask one OPEN question (not yes/no) that makes "
                "them check that part again, for example where or how it happens.")
    elif mv == "reveal_plant":
        m = session.misconception(move["mid"])
        task = (f'REVEAL: kindly admit your earlier statement was a trick to test both of you, and say the right '
                f'idea simply: "{m["correction"]}". Reassure them it is a common mix-up, then ask what they think now.')
    elif mv == "probe_error":
        e = move["error"]
        task = (f'The student said something that is not right: "{e["claim"]}" (correct idea: "{e["correction"]}"). '
                "Do not correct them. Ask a puzzled question that makes them rethink it themselves.")
    else:  # wrap_up
        strong = [session.concept(cid)["name"] for cid, c in session.concepts.items() if c["status"] == "explained"]
        weak = [session.concept(cid)["name"] for cid, c in session.concepts.items() if c["status"] != "explained"]
        fixes = "; ".join(e["correction"] for e in session.student_errors if not e["resolved"])
        task = ("Wrap up: thank them for teaching you. Be encouraging but honest; do not call them an amazing "
                "or perfect teacher if parts were shaky. Praise ONLY these, which they really explained "
                f"themselves: {', '.join(strong) or 'their effort and willingness to try'}. ")
        if weak:
            task += f"You MUST name these as things to go over together next time: {', '.join(weak)}. "
        if fixes:
            task += f"Gently clear up, as something you both learned today: {fixes}. "
        task += "No question."
    return " ".join(notes + [f"Instruction: {task}"])


def respond(session: Session, move: dict, changes: dict, feedback: str = "") -> str:
    """Reflect-Respond (Jin et al., CHI 2024): the classmate answers from its notebook, not from the lesson card."""
    cfg = session.cfg
    notebook = "\n".join(f"- {n['text']}" for n in session.learned) or "- (nothing yet)"
    system = PERSONA_PROMPT.format(
        name=cfg["name"], persona=cfg["persona"], topic=session.card["topic"],
        level_style=LEVEL_QUESTIONS[session.level], notebook=notebook,
    )
    note = f"[director note, not from the student] {_instruction(session, move, changes)}"
    if feedback:
        note += f"\n{feedback}"
    messages = [{"role": "system", "content": system}, *session.history[-8:], {"role": "user", "content": note}]
    return llm.chat(messages)


def _speak(session: Session, move: dict, changes: dict) -> tuple[str, dict | None]:
    """Write the classmate's line, then fact-check it (verify.py) before the student sees it."""
    reply = respond(session, move, changes)
    if not session.fact_check:
        return reply, None
    reply, check = verify.checked(session, move, reply, lambda fb: respond(session, move, changes, fb),
                                  use_llm=move["move"] != "opening")
    true = sum(v["verdict"] == "true" for v in check["verdicts"])
    session.checks.append({
        "turn": session.turn, "move": move["move"], "claims": len(check["verdicts"]), "true": true,
        "unverified": sum(v["verdict"] == "unverified" for v in check["verdicts"]),
        "false_caught": check["first_false"], "leaks_caught": len(check["first_leaks"]),
        "regenerated": check["regenerated"], "plant_present": check["plant_present"], "ok": check["ok"],
    })
    return reply, check


# ---------------------------------------------------------------- session API


def start(session: Session) -> str:
    reply, check = _speak(session, {"move": "opening"}, {})
    session.history.append({"role": "assistant", "content": reply})
    session.last_move = "opening"
    session.opening_check = check
    return reply


def step(session: Session, message: str) -> tuple[str, dict]:
    """Process one student message. Returns (buddy reply, debug info)."""
    session.turn += 1
    ev = evaluate(session, message)
    session.history.append({"role": "user", "content": message})
    changes = update(session, ev)
    move = choose_move(session)
    reply, check = _speak(session, move, changes)
    session.history.append({"role": "assistant", "content": reply})
    session.last_move = move["move"]
    if move["move"] == "wrap_up":
        session.done = True
    return reply, {"eval": ev, "move": move["move"], "level": session.level, "check": check}


def fact_check_summary(session: Session) -> dict:
    c = session.checks
    plants = [x for x in c if x["move"] == "plant"]
    return {
        "lines": len(c), "claims": sum(x["claims"] for x in c), "claims_true": sum(x["true"] for x in c),
        "claims_unverified": sum(x["unverified"] for x in c), "false_caught": sum(x["false_caught"] for x in c),
        "leaks_caught": sum(x["leaks_caught"] for x in c), "regenerated": sum(x["regenerated"] for x in c),
        "shown_clean": sum(x["ok"] for x in c),
        "plants_verified": f'{sum(bool(x["plant_present"]) for x in plants)}/{len(plants)}',
    }


def report(session: Session) -> dict:
    weights = {"explained": 1.0, "partial": 0.5, "not_covered": 0.0}
    open_errors = {e.get("concept_id") for e in session.student_errors if not e["resolved"]}
    concepts = []
    for c in session.card["concepts"]:
        status = session.concepts[c["id"]]["status"]
        if c["id"] in open_errors and status == "explained":  # still holds a wrong idea about it
            status = "partial"
        concepts.append({"name": c["name"], "status": status, "said": session.concepts[c["id"]]["said"]})
    plants = [
        {"statement": m["wrong_statement"], "outcome": session.plants[m["id"]]["status"], "correction": m["correction"]}
        for m in session.card["misconceptions"] if session.plants[m["id"]]["status"] != "unused"
    ]
    answered = [p for p in plants if p["outcome"] != "planted"]  # "planted" = session ended before an answer
    caught = sum(p["outcome"] == "caught" for p in plants)
    caught_hint = sum(p["outcome"] == "caught_after_hint" for p in plants)
    unanswered = len(plants) - len(answered)
    progress = {}
    if session.memory.get("previous_mastery") is not None:
        progress["previous_mastery"] = session.memory["previous_mastery"]
    if session.retest:
        progress["retested_mistakes"] = [
            {"statement": session.misconception(mid)["wrong_statement"], "outcome": session.plants[mid]["status"]}
            for mid in session.retest
        ]
    return {
        "topic": session.card["topic"],
        "band": session.cfg["label"],
        "turns": session.turn,
        "mastery": round(100 * sum(weights[c["status"]] for c in concepts) / len(concepts)),
        "concepts": concepts,
        "mistakes_caught": (f"{caught}/{len(answered)} on their own, {caught_hint} after a hint"
                            + (f", {unanswered} not answered yet" if unanswered else "") if plants
                            else "not tested (no mistake was planted)"),
        "planted": plants,
        "student_errors": [
            {"claim": e["claim"], "correction": e["correction"], "resolved": e["resolved"]} for e in session.student_errors
        ],
        "avg_quality": round(sum(session.quality) / len(session.quality), 2) if session.quality else 0,
        "deeper_questions": f"{session.deep_good}/{session.deep_asked} answered well",
        "level_trace": session.level_trace,
        "fact_check": fact_check_summary(session),
        "notebook": session.learned,
        **({"progress": progress} if progress else {}),
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from dotenv import load_dotenv

    here = Path(__file__).resolve().parent
    load_dotenv(here / ".env")
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--band", choices=BANDS, default="middle")
    ap.add_argument("--card", default=str(here / "eval" / "photosynthesis_card.json"))
    ap.add_argument("--student", help="name for cross-session memory (profiles/<name>.json)")
    args = ap.parse_args()

    import memory

    data = json.loads(Path(args.card).read_text(encoding="utf-8"))
    card = data["card"]
    profile = memory.load(args.student) if args.student else None
    s = Session(card=card, band=args.band, memory=memory.for_session(profile, card["topic"]) if profile else {},
                page_text=data["page_text"])
    print(f"{s.cfg['name']}: {start(s)}")
    while not s.done:
        msg = input("\nYou: ").strip()
        if msg in ("quit", "exit"):
            break
        reply, info = step(s, msg)
        print(f"  [{info['move']} | level {info['level']}]")
        print(f"{s.cfg['name']}: {reply}")
    rep = report(s)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if profile is not None and s.turn:
        print("memory saved to", memory.save(memory.record(profile, s, rep)))
