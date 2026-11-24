"""Compare word-by-word, whole-sentence, hybrid and "bare" conversion on a small test set.

    python eval/compare_modes.py --offline  # re-score the committed answers, no key needed
    python eval/compare_modes.py            # call the API for anything not yet cached

The modes:

* word      each word on its own (what a keyboard does as you type), built by the app's
            own Converter, so punctuation, digits and caching behave exactly as in the app
* sentence  the whole line in one call, script named in the instruction (what the app sends)
* hybrid    the app's default: a free cache-only draft while typing, then the sentence pass.
            What gets sent equals sentence mode; what's measured here is the draft, replaying
            each language's sentences in order as one conversation (no extra API calls)
* bare      the whole line with no instruction, only the `language` field. PR #39 (government
            scheme summariser) first reported that this request shape ignores `language`;
            it's included to measure what the instruction adds.

Scores against the closest accepted reference: character error rate (CER, lower is
better), word accuracy (1 − word error rate), exact match, and p50/p95 latency.

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
import threading
import time
import unicodedata
from pathlib import Path

RECIPE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RECIPE))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(RECIPE / ".env")

from bodhan_client import LANGUAGES, BodhanError, Client, Result  # noqa: E402
from converter import LRU, Converter, tokenize, word_key  # noqa: E402

TEST_SET = Path(__file__).with_name("test_set.json")
RESULTS = Path(__file__).with_name("results")
CACHE = RESULTS / "api_answers.json"
REPORT = RESULTS / "compare_modes.json"
MODES = ("word", "sentence", "bare")


# ---- scoring -----------------------------------------------------------------------


def normalize(s: str) -> str:
    """NFC, drop punctuation the model sometimes adds, collapse spaces."""
    s = unicodedata.normalize("NFC", s)
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("P"))
    return " ".join(s.split())


def edit_distance(a, b) -> int:
    """Levenshtein distance between two sequences (strings or lists of words)."""
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


def word_accuracy(hyp: str, refs: list[str]) -> float:
    """1 − word error rate against the closest accepted reference, floored at 0."""
    h = normalize(hyp).split()
    best = min(edit_distance(h, normalize(r).split()) / max(len(normalize(r).split()), 1) for r in refs)
    return max(0.0, 1.0 - best)


def percentile(values: list[float], q: int) -> float:
    """Inclusive percentile (q in 1..99), e.g. p50 and p95."""
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1]


# ---- cached API access -------------------------------------------------------------


class CachedCaller:
    """Calls the API once per distinct request and remembers (text, latency) on disk."""

    def __init__(self, client: Client | None, offline: bool):
        self.client, self.offline = client, offline
        self.data = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        self.new_calls = 0
        self._lock = threading.Lock()   # the Converter asks for words from several threads

    def _save(self) -> None:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    def get(self, kind: str, text: str, language: str, sample: int = 0) -> dict:
        key = f"{kind}|{language}|{text}" + (f"#{sample}" if sample else "")
        with self._lock:
            if key in self.data:
                return self.data[key]
            if self.offline:
                raise LookupError(f"not cached: {key}")
            entry = self._fetch(kind, text, language)
            self.data[key] = entry
            self.new_calls += 1
            self._save()
            return entry

    def _fetch(self, kind: str, text: str, language: str) -> dict:
        for _ in range(5):
            try:
                return self._call(kind, text, language)
            except BodhanError as exc:
                if exc.code == "empty_answer":     # an empty answer is a result, not a reason to stop
                    return {"text": "", "latency_ms": 0.0, "kind": kind, "route": self.client.route}
                # The model throttle answers 429 with Retry-After (up to ~1 min). A batch job can wait.
                if exc.status != 429 or exc.is_budget:
                    raise
                wait = (exc.retry_after or 30) + 1
                print(f"  throttled after {self.new_calls} new calls; waiting {wait:.0f} s")
                time.sleep(wait)
            except requests.RequestException as exc:
                print(f"  {type(exc).__name__} on {text!r}; waiting 15 s")
                time.sleep(15)
        raise BodhanError(503, "unavailable", f"gave up on {text!r} after 5 attempts")

    def _call(self, kind: str, text: str, language: str) -> dict:
        """One API request of the given kind. ``kind`` says what was asked; ``route`` says
        which HTTP route answered."""
        if kind == "instructed":            # roman → native, script named in the instruction
            r = self.client.transliterate(text, language, "native")
        elif kind == "bare":                # roman → native, `language` field only
            r = self.client.raw_chat(text, language=language, script="native")
        elif kind == "roman-instructed":    # native → roman, with the instruction
            r = self.client.transliterate(text, language, "roman")
        elif kind == "roman-bare":          # native → roman, `language`/`script` fields only
            r = self.client.raw_chat(text, language=language, script="roman")
        else:
            raise ValueError(f"unknown request kind {kind!r}")
        return {"text": r.text, "latency_ms": r.latency_ms, "kind": kind, "route": r.route}


class CacheClient(Client):
    """Feeds the app's Converter from CachedCaller, so the eval runs the app's own code."""

    def __init__(self, caller: CachedCaller):   # no key needed: never touches the network itself
        self.caller, self.route = caller, "cache"

    def transliterate(self, text: str, language: str, script: str = "native") -> Result:
        e = self.caller.get("instructed", text, language)
        if not e["text"]:   # mirror the real client, which raises on an empty answer
            raise BodhanError(502, "empty_answer", "the API returned an empty answer")
        return Result(e["text"], e["latency_ms"], "cache", False)


# ---- one sentence ------------------------------------------------------------------


def run_item(caller: CachedCaller, conv: Converter, item: dict, repeats: int) -> dict:
    lang, roman, refs = item["language"], item["roman"], item["native"]
    row = {"split": item.get("split", "dev"), "language": lang, "roman": roman, "references": refs}

    # word: the app's Converter with an empty cache, fed from the cached per-word answers.
    # The calls were recorded one after another; the app makes them in parallel, so the
    # latency reported is the slowest single word.
    words = sorted({word_key(p) for p, is_word in tokenize(roman) if is_word})
    per_word = {w: caller.get("instructed", w, lang) for w in words}
    conv.cache = LRU()                      # each sentence starts with an empty word cache
    word = conv.word(roman, lang)
    row["word"] = {"text": word.text, "calls": word.calls,
                   "latency_ms": max(a["latency_ms"] for a in per_word.values()),
                   "per_word": {w: a["text"] for w, a in per_word.items()}}

    s = caller.get("instructed", roman, lang)
    samples = [s["text"]] + [caller.get("instructed", roman, lang, sample=i)["text"] for i in range(1, repeats + 1)]
    row["sentence"] = {"text": s["text"], "calls": 1, "latency_ms": s["latency_ms"],
                       "samples": samples, "stable": len(set(samples)) == 1,
                       "worst_sample_cer": round(max(cer(x, refs) for x in samples), 3)}
    b = caller.get("bare", roman, lang)
    row["bare"] = {"text": b["text"], "calls": 1, "latency_ms": b["latency_ms"]}
    for m in MODES:
        row[m]["cer"] = round(cer(row[m]["text"], refs), 3)
        row[m]["word_accuracy"] = round(word_accuracy(row[m]["text"], refs), 3)
        row[m]["exact"] = row[m]["cer"] == 0
    return row


def replay_hybrid(conv: Converter, rows: list[dict]) -> dict:
    """Hybrid mode as the app runs it: per language, one conversation in test-set order.
    Before each sentence pass, record what the free cache-only draft could already show;
    then the (cached) sentence answer is sent and seeds the word cache."""
    known = total = right = 0
    per_language = {}
    for lang in sorted({r["language"] for r in rows}):
        conv.cache = LRU()                  # each language is a new conversation
        k = t = 0
        for r in (r for r in rows if r["language"] == lang):
            draft = conv.convert(r["roman"], lang, "draft")
            ref_words = {w for x in r["references"] for w in normalize(x).split()}
            for w in draft.words:
                t += 1
                if w["native"] != w["roman"]:
                    k += 1
                    right += normalize(w["native"]) in ref_words
            conv.sentence(r["roman"], lang)      # what gets sent; teaches the cache
            r["hybrid_draft"] = draft.text
        per_language[lang] = f"{k}/{t}"
        known, total = known + k, total + t
    return {"draft_words_shown_in_script": f"{known}/{total}",
            "draft_words_correct": f"{right}/{known}" if known else "0/0",
            "per_language": per_language,
            "sent_text": "identical to sentence mode",
            "calls_per_message": 1}


def summarize(rows: list[dict]) -> dict:
    out = {}
    for split in ("dev", "heldout", "all"):
        subset = [r for r in rows if split in ("all", r["split"])]
        if not subset:
            continue
        out[split] = {}
        for m in MODES:
            lat = [r[m]["latency_ms"] for r in subset]
            out[split][m] = {
                "mean_cer": round(statistics.mean(r[m]["cer"] for r in subset), 3),
                "word_accuracy": round(statistics.mean(r[m]["word_accuracy"] for r in subset), 3),
                "exact_match": f"{sum(r[m]['exact'] for r in subset)}/{len(subset)}",
                "p50_latency_ms": round(percentile(lat, 50)),
                "p95_latency_ms": round(percentile(lat, 95)),
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

    conv = Converter(CacheClient(caller))   # the app's own code, fed from the saved answers
    rows = []
    for item in items:
        try:
            rows.append(run_item(caller, conv, item, args.repeats))
        except (BodhanError, LookupError) as exc:
            # Never overwrite the committed report with a partial one.
            print(f"Stopped at {item['roman']!r}: {exc}. Answers so far are cached; rerun to continue. "
                  f"{REPORT.name} not updated.")
            conv.close()
            return 1
    summary = summarize(rows)
    summary["hybrid"] = replay_hybrid(conv, rows)
    conv.close()
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n{'split':<8}{'lang':<5}{'roman':<32}{'word':<34}{'sentence':<34}bare")
    for r in rows:
        word, sent, bare = (f"{r[m]['text']} ({r[m]['cer']:.2f})" for m in MODES)
        flag = "" if r["sentence"]["stable"] else "  [sentence answer varied]"
        print(f"{r['split']:<8}{r['language']:<5}{r['roman']:<32}{word:<34}{sent:<34}{bare}{flag}")
    for split in ("dev", "heldout", "all"):
        if split not in summary:
            continue
        table = summary[split]
        print(f"\n{split}:\n| Mode | Mean CER | Word accuracy | Exact match | p50 | p95 | API calls |\n|---|---|---|---|---|---|---|")
        for m in MODES:
            s = table[m]
            print(f"| {m} | {s['mean_cer']:.3f} | {s['word_accuracy']:.3f} | {s['exact_match']} | "
                  f"{s['p50_latency_ms']} ms | {s['p95_latency_ms']} ms | {s['calls']} |")
        st = table["sentence_stability"]
        print(f"sentence mode gave the same answer in all {st['samples_per_sentence']} samples for "
              f"{st['same_answer_every_time']} sentences; mean worst-sample CER {st['mean_worst_sample_cer']:.3f}")
    h = summary["hybrid"]
    print(f"\nhybrid: drafts showed {h['draft_words_shown_in_script']} words in script before the sentence pass "
          f"({h['draft_words_correct']} of those correct); per language {h['per_language']}; 1 call per message.")
    print(f"\n{len(rows)} sentences in {', '.join(sorted({LANGUAGES[r['language']] for r in rows}))}. "
          f"New API calls this run: {caller.new_calls}. Saved {REPORT.relative_to(RECIPE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
