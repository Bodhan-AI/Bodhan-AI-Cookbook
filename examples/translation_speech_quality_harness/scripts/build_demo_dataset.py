"""Build sample_data/demo.jsonl from Simple English Wikipedia (CC BY-SA 4.0) plus a few
Markdown / LaTeX items written for this recipe (CC0).

    python scripts/build_demo_dataset.py            # fetch (cached) + build
    python scripts/build_demo_dataset.py --offline  # build from the cached downloads only

Downloads go to outputs/wiki_raw/ (git-ignored). Selection is deterministic: the same
downloads always give the same dataset. Every item records its license and source URL.
Mix per language: ~40% single sentences, ~40% paragraphs with numbers / names, ~20% Markdown.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "outputs" / "wiki_raw"
OUT = ROOT / "sample_data" / "demo.jsonl"
API = "https://simple.wikipedia.org/w/api.php"
UA = "bodhan-eval-recipe/0.1 (translation quality harness; dataset builder)"
LANGS = ("hi", "ta", "bn")
PER_LANG = {"short": 14, "para": 15, "md": 7}
WIKI_LICENSE = "CC BY-SA 4.0 (Simple English Wikipedia; text selected and lightly reformatted)"
CC0 = "CC0-1.0 (written for this recipe)"

TITLES = [
    # science and nature
    "Water", "Sun", "Moon", "Earth", "Mars", "Photosynthesis", "Volcano", "Earthquake",
    "Monsoon", "Tiger", "Elephant", "Honey bee", "Coral reef", "Rainforest", "Glacier",
    # geography and places
    "India", "Ganges", "Himalayas", "Mumbai", "Kolkata", "Chennai", "Kerala", "Sri Lanka",
    "Bay of Bengal", "Thar Desert",
    # health and society
    "Vaccine", "Malaria", "Diabetes", "Tuberculosis", "Blood", "Heart", "Library",
    "Railway", "Electricity", "Internet",
    # history and culture
    "Taj Mahal", "Mahatma Gandhi", "Rabindranath Tagore", "Cricket", "Chess", "Diwali",
    "Indian Space Research Organisation", "Chandrayaan-3", "Solar System", "Olympic Games",
]  # fmt: skip

# Markdown / LaTeX items written for this recipe. General knowledge, not copied text.
HANDWRITTEN_MD = [
    "## Planets near the Sun\n\n| Planet | Distance from Sun (million km) | Moons |\n"
    "|---|---|---|\n| Mercury | 58 | 0 |\n| Venus | 108 | 0 |\n"
    "| Earth | 150 | 1 |\n| Mars | 228 | 2 |",
    "## Train timetable\n\n| Train | From | To | Departure |\n|---|---|---|---|\n"
    "| Rajdhani Express | New Delhi | Mumbai | 16:55 |\n"
    "| Duronto Express | Kolkata | Chennai | 05:45 |",
    "The area of a circle is $A = \\pi r^2$. If the radius is 7 cm, the area is about "
    "154 square centimetres.",
    "Speed is distance divided by time: $v = \\frac{d}{t}$. A car that travels 120 km in "
    "2 hours has an average speed of 60 km/h.",
    "## Daily water intake\n\n- Children: about **1.5 litres**\n- Adults: about **2.5 litres**\n"
    "- People who work outdoors in summer may need **more**.",
    "| Month | Rainfall in Mumbai (mm) |\n|---|---|\n| June | 500 |\n| July | 840 |\n"
    "| August | 580 |\n| September | 330 |",
    "Einstein's famous equation is $E = mc^2$, where $c$ is the speed of light, about "
    "300,000 km per second.",
    "## How to save electricity\n\n1. Switch off lights when you leave a room.\n"
    "2. Use LED bulbs instead of old bulbs.\n3. Unplug chargers that are not in use.",
    "| Vaccine | Protects against | Usual age |\n|---|---|---|\n| BCG | Tuberculosis | At birth |\n"
    "| MMR | Measles, mumps, rubella | 9 to 12 months |",
    "The water cycle has four main steps: **evaporation**, **condensation**, "
    "**precipitation** and **collection**. The Sun's heat drives the whole cycle.",
    "## Cricket scores\n\n| Team | Runs | Wickets |\n|---|---|---|\n| India | 287 | 6 |\n"
    "| Australia | 251 | 10 |\n\nIndia won by 36 runs.",
]  # fmt: skip

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_PRONOUN_START = re.compile(r"^(It|Its|This|These|That|Those|He|She|They|His|Her|Their|There)\b")
# Broken source text: a lowercase word starting a sentence ("Sanskrit. mahatma is"), or a
# capitalised common word glued mid-sentence ("Karna lived Like he").
_GARBLED = re.compile(r"\. [a-z]|[a-z] (Like|The|And|But|So|He|She|They|It) [a-z]")
_NON_LATIN = re.compile(r"[^\x00-\u024F\u2000-\u206F°–—’“”]")  # the source must be English
MAX_PER_ARTICLE = 4
_BAD = re.compile(
    r"\bca\.|\bc\. |\be\.g\.|\bi\.e\.|\bet al|may refer to|disambiguation|\bISBN\b"
    r"|\[|\]|\{|\}|=|http|;|\bp\.\s|\(\s*\)"
)


def fetch(title: str, offline: bool) -> dict | None:
    """Plain-text intro + first sections of one article (cached as JSON)."""
    path = RAW / (re.sub(r"[^A-Za-z0-9]+", "_", title) + ".json")
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    if offline:
        return None
    q = urllib.parse.urlencode({
        "action": "query", "prop": "extracts|info", "explaintext": 1, "exsectionformat": "plain",
        "inprop": "url", "redirects": 1, "titles": title, "format": "json", "formatversion": 2,
    })  # fmt: skip
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 4:
                raise
            wait = float(e.headers.get("Retry-After") or 10 * (attempt + 1))
            print(f"rate limited on {title!r}; waiting {wait:.0f} s", file=sys.stderr)
            time.sleep(min(wait, 120))
    page = (data.get("query", {}).get("pages") or [{}])[0]
    if page.get("missing") or not page.get("extract"):
        return None
    rec = {"title": page["title"], "url": page.get("fullurl"), "text": page["extract"]}
    RAW.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False), "utf-8")
    time.sleep(2.0)  # be polite to the API
    return rec


def sentences(text: str) -> list[str]:
    out = []
    for para in text.split("\n"):
        para = para.strip()
        if len(para) < 40 or para.endswith(":"):
            continue  # headings, captions, list stubs
        for s in _SENT.split(para):
            s = re.sub(r"\s+", " ", s).strip()
            words = len(s.split())
            ok = 7 <= words <= 32 and s.endswith(".") and s.count("(") < 2
            clean = not (_BAD.search(s) or _GARBLED.search(s) or _NON_LATIN.search(s))
            if ok and clean:
                out.append(s)
    return out


def richness(s: str) -> int:
    """Prefer text with numbers and names: the error types that matter most."""
    return 2 * bool(re.search(r"\d", s)) + len(re.findall(r"(?<!^)(?<![.!?] )\b[A-Z][a-z]+", s))


def build(articles: list[dict], seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    shorts, paras, mds = [], [], []
    for a in articles:
        sents = sentences(a["text"])
        meta = {"license": WIKI_LICENSE, "source_title": a["title"], "source_url": a["url"]}
        shorts += [(s, meta) for s in sents[:8] if not _PRONOUN_START.match(s)][:6]
        for k in range(0, max(0, len(sents) - 1), 3):
            chunk = " ".join(sents[k : k + rng.choice((2, 3))])
            if 120 <= len(chunk) <= 420:
                paras.append((chunk, meta))
        if len(sents) >= 3:
            items = sents[1:4]
            mds.append((f"## {a['title']}\n\n" + "\n".join(f"- {s}" for s in items), meta))

    used: dict[str, int] = {}

    def pick(pool, n, key=None):
        """The n best-ranked items (shuffled among the top 3n), at most MAX_PER_ARTICLE
        items per source article across the whole dataset."""
        pool = sorted(pool, key=lambda x: (-(key(x[0]) if key else 0), x[0]))
        head = pool[: max(n * 3, n)]
        rng.shuffle(head)
        out = []
        for text, meta in head + pool[len(head) :]:
            src = meta.get("source_title")
            if src and used.get(src, 0) >= MAX_PER_ARTICLE:
                continue
            out.append((text, meta))
            if src:
                used[src] = used.get(src, 0) + 1
            if len(out) == n:
                break
        return out

    total = {k: v * len(LANGS) for k, v in PER_LANG.items()}
    hand = [(t, {"license": CC0}) for t in HANDWRITTEN_MD]
    n_hand = min(len(hand), total["md"] // 2)
    chosen = {
        "short": pick(shorts, total["short"]),
        "para": pick(paras, total["para"], key=richness),
        "md": hand[:n_hand] + pick(mds, total["md"] - n_hand),
    }
    rows = []
    for kind, items in chosen.items():
        seen = set()
        items = [x for x in items if not (x[0] in seen or seen.add(x[0]))]
        for k, (text, meta) in enumerate(items):
            lang = LANGS[k % len(LANGS)]
            rows.append({"id": f"demo-{lang}-{kind}-{k:03d}", "lang": lang, "source": text,
                         "kind": kind, **meta})  # fmt: skip
    rows.sort(key=lambda r: r["id"])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="use cached downloads only")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    articles, missing = [], []
    for t in TITLES:
        try:
            a = fetch(t, args.offline)
        except OSError as e:
            print(f"fetch failed for {t!r}: {e}", file=sys.stderr)
            a = None
        (articles.append(a) if a else missing.append(t))
    rows = build(articles, args.seed)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    by = {}
    for r in rows:
        by[(r["lang"], r["kind"])] = by.get((r["lang"], r["kind"]), 0) + 1
    print(
        f"{len(articles)} articles ({len(missing)} missing: {missing}); {len(rows)} items -> "
        f"{OUT.relative_to(ROOT)}; chars={sum(len(r['source']) for r in rows)}",
        file=sys.stderr,
    )
    print(json.dumps({f"{k[0]}/{k[1]}": v for k, v in sorted(by.items())}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
