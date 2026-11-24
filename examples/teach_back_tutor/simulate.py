"""Run the tutor against simulated students to check the evaluation and the policy.

    python simulate.py --profile strong --band middle
    python simulate.py --all

Each simulated student is the LLM playing a role with a known level of understanding, so we can
check that the tutor's scores match what that student actually knows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

import exercise  # noqa: E402
import llm  # noqa: E402
import memory  # noqa: E402
import quiz  # noqa: E402
import tutor  # noqa: E402

# What each simulated student knows. Weaker students never see the page, only what they remember,
# so they cannot "accidentally" give perfect answers.
PROFILES = {
    "strong": {
        "behaviour": "You understood the lesson well. Explain clearly in your own words, sometimes with an "
        "example. If your classmate says something that contradicts the lesson, politely correct them.",
        "knows": None,  # sees the full page
    },
    "partial": {
        "behaviour": "You remember only part of the lesson. You say 'I think...' or 'something like...'. "
        "If asked about something you don't remember, guess vaguely or admit you forgot; never invent "
        "scientific terms. You catch obvious mistakes but not subtle ones.",
        "knows": "Plants make their own food; it's called photosynthesis. It needs sunlight and water, "
        "and something from the air. It happens in the leaves, you think.",
    },
    "confused": {
        "behaviour": "You did not follow the lesson. Give short answers and tend to agree with whatever "
        "your classmate says. Never use scientific terms you were not told.",
        "knows": "Plants take their food from the soil through their roots. Sunlight helps them grow somehow.",
    },
}

STUDENT_PROMPT = """You are role-playing a {band_label} school student teaching a classmate who missed a \
lesson. {behaviour}
Reply as the student would SPEAK: 1-3 short sentences, casual, no lists. Only output the student's words.

{knowledge}"""


QUIZ_PROMPT = """Answer these quiz questions as the student you are role-playing, using ONLY what you know{when}. \
If you do not know, guess.

{questions}

Return JSON: {{"answers": [{{"n": 1, "choice": 0}}]}} where choice is the 0-based option index."""

SIM_MODEL = os.environ.get("SIM_MODEL") or None  # the role-played student can use a different model from the tutor


def take_quiz(system: str, qz: dict, transcript: list | None = None) -> int:
    when = ""
    if transcript:
        talk = "\n".join(f"{'Classmate' if w == 'buddy' else 'You'}: {t}" for w, t, *_ in transcript)
        when = f" and what came up while you taught your classmate:\n{talk}\n"
    prompt = QUIZ_PROMPT.format(when=when, questions=quiz._as_text(qz["questions"]))
    try:
        got = llm.chat_json([{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                            temperature=0, model=SIM_MODEL)
    except ValueError:
        got = {}  # unanswered questions score 0
    picks = {int(a["n"]): a.get("choice") for a in got.get("answers", []) if str(a.get("n", "")).isdigit()}
    return quiz.score(qz, [picks.get(i) for i in range(1, len(qz["questions"]) + 1)])


def run(profile: str, band: str, card_file: Path, student_memory: dict | None = None) -> dict:
    data = json.loads(card_file.read_text(encoding="utf-8"))
    page, card = data["page_text"], data["card"]
    s = tutor.Session(card=card, band=band, memory=student_memory or {}, page_text=page)
    prof = PROFILES[profile]
    knowledge = (f'The lesson page:\n"""{page}"""' if prof["knows"] is None
                 else f"All you remember from the lesson: {prof['knows']}")
    system = STUDENT_PROMPT.format(band_label=tutor.BANDS[band]["label"], behaviour=prof["behaviour"],
                                   knowledge=knowledge)
    qz = quiz.build(card, page)
    quiz_pre = take_quiz(system, qz) if qz["questions"] else None

    transcript = [("buddy", tutor.start(s), "opening", s.level)]
    latencies = []
    while not s.done:
        convo = [{"role": "system", "content": system}] + [
            {"role": "user" if who == "buddy" else "assistant", "content": text} for who, text, *_ in transcript
        ]
        student_msg = llm.chat(convo, temperature=0.8, model=SIM_MODEL)
        transcript.append(("student", student_msg, "", s.level))
        t0, waited0 = time.perf_counter(), llm.STATS["throttled_seconds"]
        reply, info = tutor.step(s, student_msg)
        latencies.append((time.perf_counter() - t0, llm.STATS["throttled_seconds"] - waited0))
        transcript.append(("buddy", reply, info["move"], info["level"]))

    rep = tutor.report(s)
    rep["profile"] = profile
    rep["moves"] = [m for who, _, m, _ in transcript if who == "buddy"]
    rep["avg_turn_seconds"] = round(sum(t for t, _ in latencies) / len(latencies), 1)
    rep["avg_turn_seconds_unthrottled"] = round(sum(t - w for t, w in latencies) / len(latencies), 1)
    rep["quiz_pre"] = quiz_pre
    rep["quiz_post"] = take_quiz(system, qz, transcript) if qz["questions"] else None
    rep["quiz_total"] = len(qz["questions"])
    return {"report": rep, "transcript": transcript, "session": s}


# Homework mode: the simulated student helps the classmate solve an exercise. They never see the answer key.
HW_PROFILES = {
    "solver": "You know how to solve this. Explain one step at a time in your own words, then give the answer.",
    "stuck": "You do not know how to solve this and cannot work it out alone: answer 'I'm not sure' or make a "
    "vague guess. Only when your classmate offers a half-finished idea or works out a step, take that ONE step "
    "further. Never jump ahead to the final answer by yourself.",
    "slip": "You make one mistake at first (a wrong calculation or a wrong idea). If your classmate seems puzzled, "
    "check your work and fix it, then carry on to the answer.",
}

HW_STUDENT_PROMPT = """You are role-playing a {band_label} school student helping a classmate with this homework \
question: "{question}"
{behaviour}
Reply as the student would SPEAK: 1-3 short sentences, casual, no lists. Only output the student's words.
The page it comes from:
\"\"\"{page}\"\"\""""


def run_homework(profile: str, band: str, page: str, question: str, ref: dict) -> dict:
    hw = exercise.Homework(question=question, ref=ref, band=band, page_text=page)
    # the stuck student has not read the page (with it, a capable model just solves the question: tested, it did)
    seen = "(you have not read this page)" if profile == "stuck" else page
    system = HW_STUDENT_PROMPT.format(band_label=tutor.BANDS[band]["label"], behaviour=HW_PROFILES[profile],
                                      question=question, page=seen)
    line, check = exercise.start(hw)
    transcript = [("buddy", line, "opening", check)]
    while not hw.done:
        convo = [{"role": "system", "content": system}] + [
            {"role": "user" if who == "buddy" else "assistant", "content": text} for who, text, *_ in transcript]
        msg = llm.chat(convo, temperature=0.8, model=SIM_MODEL)
        transcript.append(("student", msg, "", None))
        line, info = exercise.step(hw, msg)
        transcript.append(("buddy", line, info["move"], info["check"]))
    rep = exercise.report(hw)
    moves = [m for who, _, m, _ in transcript if who == "buddy"]
    checks = [c for who, _, _, c in transcript if who == "buddy"]
    rep.update(profile=profile, moves=moves,
               revealed=sum(s == "revealed" for s in hw.steps),
               drafts_blocked=sum(bool(c["first_problems"]) for c in checks),
               leaks_reaching_student=sum(not c["ok"] for c in checks))
    return {"report": rep, "transcript": [(w, t, m) for w, t, m, _ in transcript]}


def homework_sims(band: str) -> list[dict]:
    """Each homework student on two questions from the frozen photosynthesis page (a sum and a why question)."""
    data = json.loads((HERE / "eval" / "photosynthesis_card.json").read_text(encoding="utf-8"))
    items = json.loads((HERE / "eval" / "exercise_labels.json").read_text(encoding="utf-8"))["items"]
    page, rows = data["page_text"], []
    for item in (items[0], items[6]):  # "A leaf makes 12 grams ... one third" and "dark cupboard"
        ref = exercise.solve(item["question"], page, band)
        for p in HW_PROFILES:
            res = run_homework(p, band, page, item["question"], ref)
            r = res["report"]
            (HERE / "outputs" / f"sim_hw_{p}_{items.index(item) + 1}.json").write_text(
                json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            rows.append({k: r[k] for k in ("profile", "question", "agreement", "solved", "steps_on_own", "nudges",
                                           "revealed", "errors", "turns", "avg_quality", "drafts_blocked",
                                           "rewritten", "leaks_reaching_student", "moves")})
            print(f"  hw {p:7} solved={r['solved']!s:5} own={r['steps_on_own']} nudges={r['nudges']} "
                  f"revealed={r['revealed']} blocked={r['drafts_blocked']} leaks={r['leaks_reaching_student']} "
                  f"turns={r['turns']}  {item['question'][:40]}")
    return rows


def print_run(result: dict) -> None:
    for who, text, move, level in result["transcript"]:
        tag = f"  [{move} | L{level}]" if who == "buddy" else ""
        print(f"{who.upper():8}{tag}\n  {text}\n")
    print(json.dumps(result["report"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=PROFILES, default="strong")
    ap.add_argument("--band", choices=tutor.BANDS, default="middle")
    ap.add_argument("--all", action="store_true", help="every profile, summary only")
    ap.add_argument("--card", default=str(HERE / "eval" / "photosynthesis_card.json"))
    ap.add_argument("--sessions", type=int, default=1,
                    help="run this many sessions per student, carrying memory between them")
    ap.add_argument("--homework", action="store_true", help="simulate Homework mode instead")
    args = ap.parse_args()

    if args.homework:  # shown in the README and the app's Evaluation view
        hw_rows = homework_sims(args.band)
        results = HERE / "eval" / "results.json"
        data = json.loads(results.read_text(encoding="utf-8")) if results.exists() else {}
        data["homework_sim"] = {"date": date.today().isoformat(), "band": args.band, "rows": hw_rows,
                                "model": os.environ.get("LLM_MODEL", "") + (f" (student: {SIM_MODEL})" if SIM_MODEL else "")}
        results.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(0)

    def save(res: dict, name: str) -> None:
        out = {k: v for k, v in res.items() if k != "session"}
        (HERE / "outputs" / f"{name}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    def row(label: str, r: dict) -> str:
        fc = r["fact_check"]
        return (f"{label:12} mastery={r['mastery']:3}%  caught=[{r['mistakes_caught']}] "
                f"errors={len(r['student_errors'])}  quality={r['avg_quality']}  turns={r['turns']}  "
                f"quiz {r['quiz_pre']}->{r['quiz_post']}/{r['quiz_total']}  "
                f"fact-check: {fc['claims_true']}/{fc['claims']} claims true, {fc['false_caught']} false + "
                f"{fc['leaks_caught']} leaks blocked, {fc['regenerated']} rewritten  "
                f"levels={r['level_trace']}  {r['avg_turn_seconds']}s/turn "
                f"({r['avg_turn_seconds_unthrottled']}s without rate-limit waits)")

    summary = []

    topic = json.loads(Path(args.card).read_text(encoding="utf-8"))["card"]["topic"]
    rows = []
    for p in (PROFILES if args.all else [args.profile]):
        profile = {"student": f"sim_{p}", "level": None, "topics": {}, "sessions": []}  # in memory only
        for n in range(1, args.sessions + 1):
            res = run(p, args.band, Path(args.card), memory.for_session(profile, topic))
            memory.record(profile, res["session"], res["report"])
            suffix = f"_s{n}" if args.sessions > 1 else ""
            save(res, f"sim_{p}_{args.band}{suffix}")
            rows.append(row(f"{p}{suffix}", res["report"]))
            r = res["report"]
            summary.append({"profile": f"{p}{suffix}", "mastery": r["mastery"], "tricks": r["mistakes_caught"],
                            "detail": " ".join(PROFILES[p]["behaviour"].split(". ")[:1]) + ".",
                            "quiz_pre": r["quiz_pre"], "quiz_post": r["quiz_post"], "quiz_total": r["quiz_total"],
                            "checked_lines": r["fact_check"]["lines"], "fact_check": r["fact_check"],
                            "turns": r["turns"], "avg_quality": r["avg_quality"]})
            if not args.all:
                print_run(res)
            if "progress" in res["report"]:
                rows.append(f"{'':12} progress: {json.dumps(res['report']['progress'], ensure_ascii=False)}")
    print("\n".join(rows))
    if args.all:  # shown in the app's Evaluation view
        results = HERE / "eval" / "results.json"
        data = json.loads(results.read_text(encoding="utf-8")) if results.exists() else {}
        data["simulation"] = {"date": date.today().isoformat(), "band": args.band,
                              "model": os.environ.get("LLM_MODEL", "") + (f" (student: {SIM_MODEL})" if SIM_MODEL else ""),
                              "rows": summary}
        results.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
