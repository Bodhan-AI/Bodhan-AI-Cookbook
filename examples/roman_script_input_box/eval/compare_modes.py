"""Compare word-by-word, whole-sentence and "bare" conversion on a small test set.

    python eval/compare_modes.py --offline  # re-score the committed answers, no key needed
    python eval/compare_modes.py            # call the API for anything not yet cached

Three ways to turn a roman line into native script:

* word      each word on its own (what a keyboard does as you type)
* sentence  the whole line in one call, script named in the instruction (what the app sends)
* bare      the whole line with no instruction, only the `language` field, as earlier
            community recipes did. Included to show why the instruction is needed.

Scores: character error rate (CER, edit distance / reference length, lower is better)
against the closest of the accepted references, exact-match rate, and latency.

The test set has two splits. ``dev`` sentences were used while designing the instruction;
``heldout`` sentences were written afterwards (with punctuation, digits and English words)
and never used to tune anything. Sentence mode is also asked ``--repeats`` extra times to
measure how often the model changes its answer.

Every API answer is stored in eval/results/api_answers.json, keyed by request, and is
committed, so the numbers in the README can be checked without a key. The key's quota is
small (120 requests; the model also throttles at ~15 calls a minute), so a run only pays
for requests it hasn't made before and a 429 halfway through loses nothing.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import unicodedata
from pathlib import Path

RECIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RECIPE))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(RECIPE / ".env")

from bodhan_client import LANGUAGES, BodhanError, Client  # noqa: E402
from converter import tokenize  # noqa: E402

TEST_SET = Path(__file__).with_name("test_set.json")
RESULTS = Path(__file__).with_name("results")
CACHE = RESULTS / "api_answers.json"
REPORT = RESULTS / "compare_modes.json"
MODES = ("word", "sentence", "bare")


def normalize(s: str) -> str:
    """NFC, drop punctuation the model sometimes adds, collapse spaces."""
    s = unicodedata.normalize("NFC", s)
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("P"))
    return " ".join(s.split())


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(hyp: str, refs: list[str]) -> float:
    """Character error rate against the closest accepted reference."""
    h = normalize(hyp)
    return min(edit_distance(h, normalize(r)) / max(len(normalize(r)), 1) for r in refs)


class CachedCaller:
    """Calls the API once per distinct request and remembers (text, latency) on disk."""

    def __init__(self, client: Client | None, offline: bool):
        self.client, self.offline = client, offline
        self.data = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        self.new_calls = 0

    def _save(self) -> None:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    def get(self, kind: str, text: str, language: str, sample: int = 0) -> dict:
        key = f"{kind}|{language}|{text}" + (f"#{sample}" if sample else "")
        if key in self.data:
            return self.data[key]
        if self.offline:
            raise LookupError(f"not cached: {key}")
        for _ in range(5):
            try:
                if kind == "instructed":
                    r = self.client.transliterate(text, language, "native")
                    entry = {"text": r.text, "latency_ms": r.latency_ms, "route": r.route}
                else:
                    entry = self._bare(text, language)
                break
            except BodhanError as exc:
                # The model throttle answers 429 with Retry-After (~36 s). A batch job can wait.
                if exc.status != 429 or exc.is_budget:
                    raise
                wait = (exc.retry_after or 30) + 1
                print(f"  throttled after {self.new_calls} new calls; waiting {wait:.0f} s")
                time.sleep(wait)
            except requests.RequestException as exc:
                print(f"  {type(exc).__name__} on {text!r}; waiting 15 s")
                time.sleep(15)
        else:
            raise BodhanError(503, "unavailable", f"gave up on {text!r} after 5 attempts")
        self.data[key] = entry
        self.new_calls += 1
        self._save()
        return entry

    def _bare(self, text: str, language: str) -> dict:
        """The undocumented body used by earlier recipes: raw text plus a `language` field."""
        r = self.client.raw_chat(text, language=language, script="native")
        return {"text": r.text, "latency_ms": r.latency_ms, "route": "chat-bare"}


def run_item(caller: CachedCaller, item: dict, repeats: int) -> dict:
    lang, roman, refs = item["language"], item["roman"], item["native"]
    row = {"split": item.get("split", "dev"), "language": lang, "roman": roman, "references": refs}

    # word: one call per distinct word, rebuilt around the original spaces, digits and
    # punctuation exactly as converter.py does. The calls were made one after another; the
    # app makes them in parallel, so the latency reported is the slowest single word.
    pieces = tokenize(roman)
    answers = {p.lower(): caller.get("instructed", p.lower(), lang) for p, is_word in pieces if is_word}
    row["word"] = {
        "text": "".join(answers[p.lower()]["text"] if is_word else p for p, is_word in pieces),
        "calls": len(answers),
        "latency_ms": max(a["latency_ms"] for a in answers.values()),
        "per_word": {w: a["text"] for w, a in answers.items()},
    }
    s = caller.get("instructed", roman, lang)
    samples = [s["text"]] + [caller.get("instructed", roman, lang, sample=i)["text"] for i in range(1, repeats + 1)]
    row["sentence"] = {"text": s["text"], "calls": 1, "latency_ms": s["latency_ms"],
                       "samples": samples, "stable": len(set(samples)) == 1,
                       "worst_sample_cer": round(max(cer(x, refs) for x in samples), 3)}
    b = caller.get("bare", roman, lang)
    row["bare"] = {"text": b["text"], "calls": 1, "latency_ms": b["latency_ms"]}
    for m in MODES:
        row[m]["cer"] = round(cer(row[m]["text"], refs), 3)
        row[m]["exact"] = row[m]["cer"] == 0
    return row


def summarize(rows: list[dict]) -> dict:
    out = {}
    for split in ("dev", "heldout", "all"):
        subset = [r for r in rows if split in ("all", r["split"])]
        if not subset:
            continue
        out[split] = {}
        for m in MODES:
            out[split][m] = {
                "mean_cer": round(statistics.mean(r[m]["cer"] for r in subset), 3),
                "exact_match": f"{sum(r[m]['exact'] for r in subset)}/{len(subset)}",
                "median_latency_ms": round(statistics.median(r[m]["latency_ms"] for r in subset)),
                "calls": sum(r[m]["calls"] for r in subset),
            }
        sent = [r["sentence"] for r in subset]
        out[split]["sentence_stability"] = {
            "same_answer_every_time": f"{sum(s['stable'] for s in sent)}/{len(sent)}",
            "samples_per_sentence": len(sent[0]["samples"]),
            "mean_worst_sample_cer": round(statistics.mean(s["worst_sample_cer"] for s in sent), 3),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="score from eval/results/api_answers.json only")
    ap.add_argument("--repeats", type=int, default=2, help="extra samples of sentence mode per sentence (default 2)")
    args = ap.parse_args()

    client = None if args.offline else Client(route="chat", max_wait=0, timeout=60)
    caller = CachedCaller(client, args.offline)
    items = json.loads(TEST_SET.read_text(encoding="utf-8"))

    rows = []
    for item in items:
        try:
            rows.append(run_item(caller, item, args.repeats))
        except BodhanError as exc:
            print(f"Stopped at {item['roman']!r}: {exc}. Finished rows are cached; rerun later to continue.")
            break
        except LookupError as exc:
            print(f"Skipping {item['roman']!r} ({exc}).")

    if not rows:
        return 1
    summary = summarize(rows)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'split':<8}{'lang':<5}{'roman':<32}{'word':<34}{'sentence':<34}bare")
    for r in rows:
        word, sent, bare = (f"{r[m]['text']} ({r[m]['cer']:.2f})" for m in MODES)
        flag = "" if r["sentence"]["stable"] else "  [sentence answer varied]"
        print(f"{r['split']:<8}{r['language']:<5}{r['roman']:<32}{word:<34}{sent:<34}{bare}{flag}")
    for split, table in summary.items():
        print(f"\n{split}:\n| Mode | Mean CER | Exact match | Median latency | API calls |\n|---|---|---|---|---|")
        for m in MODES:
            s = table[m]
            print(f"| {m} | {s['mean_cer']:.3f} | {s['exact_match']} | {s['median_latency_ms']} ms | {s['calls']} |")
        st = table["sentence_stability"]
        print(f"sentence mode gave the same answer in all {st['samples_per_sentence']} samples for "
              f"{st['same_answer_every_time']} sentences; mean worst-sample CER {st['mean_worst_sample_cer']:.3f}")
    print(f"\n{len(rows)} sentences in {', '.join(sorted({LANGUAGES[r['language']] for r in rows}))}. "
          f"New API calls this run: {caller.new_calls}. Saved {REPORT.relative_to(RECIPE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
