"""Does grading still work when the student answers in Hindi? Uses all five Bodhan models.

    python evaluate_language.py          # writes eval/language.json (shown in the app's Evaluation view)

The 16 hand-labelled student turns in eval/judge_labels.json are turned into Hindi and sent through the
app's real input paths, then graded by the same grader and scored against the same labels:

- English (baseline): the labelled message as written.
- Typed Hinglish: indic-translate (en -> hi) gives the Hindi; the LLM writes it the way a student types
  romanized Hindi; the app's path is indic-transliterate (-> Devanagari) -> indic-translate (-> en).
- Spoken Hindi: indic-speak reads the Hindi aloud; the app's path is indic-transcribe -> indic-translate (-> en).

If the scores hold up, the language round trip is not costing the student credit.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

import bodhan  # noqa: E402
import llm  # noqa: E402
from evaluate_judges import EVAL, judge_eval, load_card  # noqa: E402

ROMANIZE = """Rewrite this Hindi sentence the way an Indian school student would type it on a phone in \
romanized Hindi (Hinglish): Latin letters, casual spelling, English words for science terms are fine. \
Only output the rewritten text.

{hindi}"""


def round_trip(message: str) -> dict:
    hindi = bodhan.translate(message, target_language="hi")
    typed = llm.chat([{"role": "user", "content": ROMANIZE.format(hindi=hindi)}], temperature=0)
    typed_en = bodhan.translate(bodhan.transliterate(typed, language="hi"), target_language="en")
    audio = bodhan.speak(hindi, bodhan.VOICES["hi"], "hi")
    heard = bodhan.transcribe(audio, language="hi", filename="speech.wav")
    spoken_en = bodhan.translate(heard, target_language="en")
    return {"english": message, "hindi": hindi, "typed": typed, "typed_en": typed_en,
            "heard": heard, "spoken_en": spoken_en}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    page, card = load_card()
    items = json.loads((EVAL / "judge_labels.json").read_text(encoding="utf-8"))["items"]
    cache = HERE / "outputs" / "language_trips.json"  # resume after a rate-limit stop
    trips = {int(k): v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()} if cache.exists() else {}
    cache.parent.mkdir(exist_ok=True)
    for item in items:  # one at a time: Bodhan rate-limits bursts
        if item["id"] not in trips:
            try:
                trips[item["id"]] = round_trip(item["message"])
            except bodhan.BodhanError as err:  # e.g. account budget used up: grade the turns we have
                print(f"stopped at turn {item['id']}: {err}")
                break
            cache.write_text(json.dumps(trips, ensure_ascii=False, indent=2), encoding="utf-8")
    for i, t in trips.items():
        print(f"#{i:2} typed: {t['typed'][:60]}\n    -> {t['typed_en'][:70]}\n    heard -> {t['spoken_en'][:70]}")
    out = {"date": date.today().isoformat(), "turns": len(trips), "of": len(items), "paths": {}}
    for path, key in [("English (baseline)", "english"), ("Typed Hinglish", "typed_en"), ("Spoken Hindi", "spoken_en")]:
        res = judge_eval(page, card, {i: t[key] for i, t in trips.items()}, quiet=True)
        out["paths"][path] = {k: res[k] for k in ("concept_accuracy", "over_credit", "plant_accuracy",
                                                  "quality_in_range", "error_detection")}
        print(f"{path:20} concepts {res['concept_accuracy']:.0%}  over-credit {res['over_credit']}  "
              f"plants {res['plant_accuracy']:.0%}  quality {res['quality_in_range']:.0%}")
    out["trips"] = list(trips.values())
    (EVAL / "language.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {EVAL / 'language.json'}")
