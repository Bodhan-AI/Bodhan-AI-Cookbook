"""Which of the 25 indic-transliterate languages does the chat-route instruction work for?

    python eval/language_matrix.py --offline  # re-check the committed answers, no key needed
    python eval/language_matrix.py            # call the API for anything not yet cached

A script check, not an accuracy test: for every supported language it sends two short
roman phrases with the instruction and checks that every letter of the answer is in the
language's script. For the four tested languages it also tries native → roman, with and
without the instruction (PR #39 reported that without it the input comes back unchanged).

Answers are cached in eval/results/api_answers.json (shared with compare_modes.py), and
the table is written to eval/results/language_matrix.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

RECIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RECIPE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_modes import CachedCaller  # noqa: E402  (loads .env)

from bodhan_client import LANGUAGES, BodhanError, Client  # noqa: E402
from converter import SCRIPT_OF, in_script  # noqa: E402

PHRASES = ["ravi kal ghar aayega", "namaskar"]
NATIVE = {"hi": "आप कैसे हैं", "gu": "તમે કેમ છો", "bn": "আমি ভালো আছি", "ta": "நான் நாளை வருவேன்"}
OUT = Path(__file__).with_name("results") / "language_matrix.json"


def scripts_used(text: str) -> str:
    names = {unicodedata.name(ch, "?").split()[0] for ch in text if unicodedata.category(ch)[0] in "LM"}
    return ",".join(sorted(names)) or "-"


def is_latin(text: str) -> bool:
    letters = [ch for ch in text if unicodedata.category(ch)[0] in "LM"]
    return bool(letters) and all(unicodedata.name(ch, "").startswith("LATIN") for ch in letters)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    caller = CachedCaller(None if args.offline else Client(route="chat", max_wait=0, timeout=60), args.offline)

    rows, reverse = [], []
    try:
        for lang in LANGUAGES:
            answers = [caller.get("instructed", p, lang)["text"] for p in PHRASES]
            ok = all(in_script(a, lang) for a in answers)
            rows.append({"language": lang, "name": LANGUAGES[lang], "expected_script": SCRIPT_OF[lang],
                         "answers": answers, "scripts_seen": [scripts_used(a) for a in answers], "ok": ok})
        for lang, text in NATIVE.items():
            instructed = caller.get("roman-instructed", text, lang)
            bare = caller.get("roman-bare", text, lang)
            reverse.append({"language": lang, "native": text,
                            "instructed": instructed["text"], "instructed_latin": is_latin(instructed["text"]),
                            "bare": bare["text"], "bare_unchanged": bare["text"].strip() == text})
    except (BodhanError, LookupError) as exc:
        print(f"Stopped: {exc}. Finished answers are cached; rerun to continue.")

    OUT.write_text(json.dumps({"roman_to_native": rows, "native_to_roman": reverse}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"{'code':<10}{'language':<26}{'expected':<12}{'ok':<5}answers")
    for r in rows:
        print(f"{r['language']:<10}{r['name']:<26}{r['expected_script']:<12}{'yes' if r['ok'] else 'NO':<5}"
              f"{' | '.join(r['answers'])}   [{'; '.join(r['scripts_seen'])}]")
    print(f"\nroman → native in the right script: {sum(r['ok'] for r in rows)}/{len(rows)} languages")
    for r in reverse:
        print(f"native → roman {r['language']}: instructed {r['instructed']!r} (latin: {r['instructed_latin']}); "
              f"bare {r['bare']!r} (unchanged: {r['bare_unchanged']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
