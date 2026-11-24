"""Does the chat route actually use the `language` field?

    python eval/probe_language.py

Follow-up to probe.py: Gujarati "tame kem cho" came back in Devanagari. This sends
the same text with different `language` values. If the output never changes, or an
invalid code still returns 200, the field is being ignored. Results go to
outputs/probe_language.json.
"""

from __future__ import annotations

import json

from probe import OUT, call, transliterate_body

CASES = [
    ("tame kem cho", "gu"),  # Gujarati sentence, correct code
    ("tame kem cho", "hi"),  # same text, other codes: does the output change?
    ("tame kem cho", "bn"),
    ("namaste", "gu"),       # a word that's valid in many languages, so only `language` can pick the script
    ("namaste", "hi"),
    ("tame kem cho", "xx"),  # not a real code: a 4xx means the field is validated
]


def main() -> int:
    results = [call(f"{lang}: {text}", *transliterate_body("chat", text, lang)) for text, lang in CASES]
    out = OUT.with_name("probe_language.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in results:
        try:
            answer = json.loads(r["body"])["choices"][0]["message"]["content"]
        except (KeyError, ValueError, TypeError):
            answer = r.get("body", r.get("error"))
        print(f"{r['label']:<20} -> {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
