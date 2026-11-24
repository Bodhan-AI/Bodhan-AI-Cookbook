"""Run the tutor against simulated students to check the evaluation and the policy.

    python simulate.py --profile strong --band middle
    python simulate.py --all

Each simulated student is the LLM playing a role with a known level of understanding, so we can
check that the tutor's scores match what that student actually knows.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

import llm  # noqa: E402
import memory  # noqa: E402
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


def run(profile: str, band: str, card_file: Path, student_memory: dict | None = None) -> dict:
    data = json.loads(card_file.read_text(encoding="utf-8"))
    page, card = data["page_text"], data["card"]
    s = tutor.Session(card=card, band=band, memory=student_memory or {})
    prof = PROFILES[profile]
    knowledge = (f'The lesson page:\n"""{page}"""' if prof["knows"] is None
                 else f"All you remember from the lesson: {prof['knows']}")
    system = STUDENT_PROMPT.format(band_label=tutor.BANDS[band]["label"], behaviour=prof["behaviour"],
                                   knowledge=knowledge)

    transcript = [("buddy", tutor.start(s), "opening", s.level)]
    latencies = []
    while not s.done:
        convo = [{"role": "system", "content": system}] + [
            {"role": "user" if who == "buddy" else "assistant", "content": text} for who, text, *_ in transcript
        ]
        student_msg = llm.chat(convo, temperature=0.8)
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
    return {"report": rep, "transcript": transcript, "session": s}


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
    ap.add_argument("--card", default=str(HERE / "outputs" / "lesson_card.json"))
    ap.add_argument("--sessions", type=int, default=1,
                    help="run this many sessions per student, carrying memory between them")
    args = ap.parse_args()

    def save(res: dict, name: str) -> None:
        out = {k: v for k, v in res.items() if k != "session"}
        (HERE / "outputs" / f"{name}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    def row(label: str, r: dict) -> str:
        return (f"{label:12} mastery={r['mastery']:3}%  caught=[{r['mistakes_caught']}] "
                f"errors={len(r['student_errors'])}  quality={r['avg_quality']}  turns={r['turns']}  "
                f"levels={r['level_trace']}  {r['avg_turn_seconds']}s/turn "
                f"({r['avg_turn_seconds_unthrottled']}s without rate-limit waits)")

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
            if not args.all:
                print_run(res)
            if "progress" in res["report"]:
                rows.append(f"{'':12} progress: {json.dumps(res['report']['progress'], ensure_ascii=False)}")
    print("\n".join(rows))
