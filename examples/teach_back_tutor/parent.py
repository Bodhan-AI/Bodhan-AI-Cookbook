"""Turn a session report into a short note for the parent, in the parent's language, written and spoken.

    python parent.py --report outputs/sim_partial_middle.json --lang hi
"""

from __future__ import annotations

import json

import bodhan
import llm

NOTE_PROMPT = """Write a short note to a parent about their child's study session. The child taught \
today's textbook topic ("{topic}") to a classmate, who sometimes made deliberate mistakes for the child to catch.

Facts about the session (use ONLY these; do not praise anything not listed as explained well):
{facts}

Rules:
- 3 or 4 short sentences, warm and plain, no jargon, no percentages, no lists, no greeting or sign-off.
- Be honest: mention what needs practice as clearly as what went well.
- If the classmate made a deliberate mistake, say whether the child noticed it.
- End with ONE simple question the parent can ask the child at home about the weakest part.
- Write in simple English (it will be translated)."""

PLANT_OUTCOME = {
    "caught": "Your child noticed on their own that this was wrong",
    "caught_after_hint": "Your child noticed it was wrong only after a hint",
    "revealed": "Your child did NOT notice this was wrong; the classmate then explained the right idea",
    "planted": "The session ended before your child answered",
}


def facts(rep: dict) -> str:
    """The report in plain sentences, decided in Python so the LLM only has to phrase them."""
    by_status = {s: [c["name"] for c in rep["concepts"] if c["status"] == s]
                 for s in ("explained", "partial", "not_covered")}
    lines = [f"- Explained well: {', '.join(by_status['explained']) or 'nothing yet'}",
             f"- Explained only partly: {', '.join(by_status['partial']) or 'none'}",
             f"- Not explained at all: {', '.join(by_status['not_covered']) or 'none'}"]
    for p in rep["planted"]:
        lines.append(f'- The classmate said "{p["statement"]}". {PLANT_OUTCOME[p["outcome"]]}. '
                     f'Right idea: {p["correction"]}')
    for e in rep["student_errors"]:
        state = "later corrected it" if e["resolved"] else "still believes this"
        lines.append(f'- Your child said "{e["claim"]}" and {state}. Right idea: {e["correction"]}')
    prev = rep.get("progress", {}).get("previous_mastery")
    if prev is not None:
        trend = "better than" if rep["mastery"] > prev else "about the same as" if rep["mastery"] == prev else "below"
        lines.append(f"- Compared with last time on this topic, today went {trend} before.")
    return "\n".join(lines)


def write_note(rep: dict) -> str:
    prompt = NOTE_PROMPT.format(topic=rep["topic"], facts=facts(rep))
    return llm.chat([{"role": "user", "content": prompt}], temperature=0.4)


def parent_note(rep: dict, lang: str, with_audio: bool = True) -> dict:
    """{"english", "text" (in `lang`), "audio" (WAV bytes or None)}."""
    english = write_note(rep)
    text = english if lang == "en" else bodhan.translate(english, target_language=lang)
    audio = bodhan.speak_long(text, lang) if with_audio and lang in bodhan.VOICES else None
    return {"english": english, "text": text, "audio": audio}


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    from dotenv import load_dotenv

    here = Path(__file__).resolve().parent
    load_dotenv(here / ".env")
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(here / "outputs" / "sim_partial_middle.json"))
    ap.add_argument("--lang", default="hi", help="ISO code, e.g. hi, bn, ta, te, mr")
    args = ap.parse_args()

    data = json.loads(Path(args.report).read_text(encoding="utf-8"))
    note = parent_note(data.get("report", data), args.lang)
    print(note["english"], "\n\n", note["text"], sep="")
    if note["audio"]:
        out = here / "outputs" / f"parent_note_{args.lang}.wav"
        out.write_bytes(note["audio"])
        print("\naudio:", out)
