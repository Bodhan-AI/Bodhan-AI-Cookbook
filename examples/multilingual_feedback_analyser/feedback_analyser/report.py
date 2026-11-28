"""Summarise the analysed records and write them as JSON, CSV and one self-contained HTML page."""

from __future__ import annotations

import csv
import json
from collections import Counter
from html import escape
from pathlib import Path

from .languages import LANGUAGE_NAMES

SENTIMENTS = ("positive", "neutral", "negative")
HTML_LANG = {"ks-Deva": "ks", "mni-Beng": "mni", "sd-Arab": "sd"}
RTL_SCRIPTS = {"Arabic"}


def summarise(records: list[dict], taxonomy: dict) -> dict:
    ok = [record for record in records if not record["error"]]
    themes = {name: dict.fromkeys(SENTIMENTS, 0) for name in taxonomy["themes"]}
    for record in ok:
        for aspect in record["aspects"]:
            themes[aspect["theme"]][aspect["sentiment"]] += 1
    replies = [record["reply"] for record in ok if record["reply"]]
    return {
        "total": len(records),
        "analysed": len(ok),
        "errors": len(records) - len(ok),
        "sentiment": {label: sum(record["sentiment"] == label for record in ok) for label in SENTIMENTS},
        "languages": dict(Counter(record["language"] for record in ok).most_common()),
        "channels": dict(Counter(record["channel"] for record in ok).most_common()),
        "themes": themes,
        "no_theme": sum(not record["themes"] for record in ok),
        "replies": len(replies),
        "replies_needing_review": sum(reply["needs_review"] for reply in replies),
        "replies_voiced": sum(bool(reply["audio"]) for reply in replies),
    }


def write_json(path: Path, records: list[dict], summary: dict, usage: dict) -> None:
    path.write_text(json.dumps({"summary": summary, "usage": usage, "items": records}, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, records: list[dict]) -> None:
    columns = ["id", "channel", "language", "script", "sentiment", "score", "themes", "original", "english", "reply", "reply_fidelity",
               "reply_needs_review", "reply_audio", "flags", "error"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for record in records:
            reply = record["reply"] or {}
            writer.writerow({
                "id": record["id"], "channel": record["channel"], "language": record["language"], "script": record["script"],
                "sentiment": record["sentiment"], "score": record["score"], "themes": "|".join(record["themes"]),
                "original": record["original"], "english": record["english"], "reply": reply.get("text"),
                "reply_fidelity": reply.get("fidelity"), "reply_needs_review": reply.get("needs_review"),
                "reply_audio": reply.get("audio"), "flags": " / ".join(record["flags"]), "error": record["error"],
            })


CSS = """
:root{--bg:#f7f6f2;--card:#fff;--ink:#1c1b19;--muted:#6b6862;--line:#e3e0d8;--pos:#2e7d4f;--neu:#8a8578;--neg:#b3402e;--warn:#9a6a00}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#1f1e1b;--ink:#ecebe6;--muted:#a09c93;--line:#34322d;--pos:#6cc390;--neu:#9d988c;--neg:#ee8672;--warn:#e0b34f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,"Noto Sans",sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:17px;margin:36px 0 12px}
.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:20px}
.stat,.item,.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.stat b{display:block;font-size:26px;font-variant-numeric:tabular-nums}.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.row{display:grid;grid-template-columns:150px 1fr 76px;gap:10px;align-items:center;margin:8px 0;font-variant-numeric:tabular-nums}
.bar{display:flex;height:12px;border-radius:6px;overflow:hidden;background:var(--line)}.bar i{display:block}
.positive{background:var(--pos)}.neutral{background:var(--neu)}.negative{background:var(--neg)}
.item{margin:10px 0}.item header{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}
.tag{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:1px 9px;color:var(--muted)}
.tag.s-positive{color:var(--pos);border-color:var(--pos)}.tag.s-negative{color:var(--neg);border-color:var(--neg)}.tag.warn{color:var(--warn);border-color:var(--warn)}
.orig{font-size:17px;margin:0 0 4px}.en{margin:0;color:var(--muted)}.reply{margin-top:12px;padding-top:12px;border-top:1px dashed var(--line)}
.reply p{margin:0 0 4px}audio{width:100%;max-width:420px;margin-top:8px}.flag{color:var(--warn);font-size:13px;margin:6px 0 0}
code{font-size:13px}
"""


def _bar(counts: dict) -> str:
    total = sum(counts.values())
    if not total:
        return '<div class="bar"></div>'
    parts = "".join(f'<i class="{label}" style="width:{counts[label] / total * 100:.2f}%"></i>' for label in SENTIMENTS if counts[label])
    return f'<div class="bar">{parts}</div>'


def _text_attrs(record: dict) -> str:
    language = HTML_LANG.get(record["language"], record["language"] or "")
    direction = ' dir="rtl"' if record["script"] in RTL_SCRIPTS else ""
    return f'lang="{escape(language)}"{direction}'


def _item_html(record: dict, taxonomy: dict) -> str:
    head = f'<span class="tag"><code>{escape(record["id"])}</code></span><span class="tag">{escape(record["channel"])}</span>'
    if record["error"]:
        return f'<article class="item"><header>{head}<span class="tag warn">not analysed</span></header><p class="flag">{escape(record["error"])}</p></article>'
    language = LANGUAGE_NAMES.get(record["language"], record["language"])
    written = " in roman script" if record["script"] == "Latin" and record["language"] != "en" else ""
    head += f'<span class="tag">{escape(language + written)}</span>'
    head += f'<span class="tag s-{record["sentiment"]}">{record["sentiment"]} {record["score"]:+.2f}</span>'
    for aspect in record["aspects"]:
        label = taxonomy["themes"][aspect["theme"]].get("label", aspect["theme"])
        head += f'<span class="tag s-{aspect["sentiment"]}">{escape(label)}</span>'
    body = f'<p class="orig" {_text_attrs(record)}>{escape(record["original"])}</p>'
    if record["english"] != record["original"]:
        body += f'<p class="en">{escape(record["english"])}</p>'
    for flag in record["flags"]:
        body += f'<p class="flag">{escape(flag)}</p>'
    reply = record["reply"]
    if reply:
        status = '<span class="tag warn">needs human review</span>' if reply["needs_review"] else '<span class="tag s-positive">ready to send</span>'
        check = f' <span class="muted">back-translation overlap {reply["fidelity"]:.2f}</span>' if reply["fidelity"] is not None else ""
        body += f'<div class="reply"><p class="muted">Suggested reply {status}{check}</p><p class="orig">{escape(reply["text"])}</p>'
        if reply["text"] != reply["native"]:
            body += f'<p>{escape(reply["native"])}</p>'
        if reply["text"] != reply["english"]:
            body += f'<p class="en">{escape(reply["english"])}</p>'
        for reason in reply["review_reasons"]:
            body += f'<p class="flag">{escape(reason)}</p>'
        if reply["audio"]:
            body += f'<audio controls preload="none" src="{escape(reply["audio"])}"></audio>'
        body += "</div>"
    return f'<article class="item"><header>{head}</header>{body}</article>'


def render_html(records: list[dict], summary: dict, taxonomy: dict, usage: dict, title: str) -> str:
    stats = [
        (summary["total"], "pieces of feedback"), (summary["sentiment"]["negative"], "negative"), (summary["sentiment"]["positive"], "positive"),
        (len(summary["languages"]), "languages"), (summary["replies"], "replies drafted"), (summary["replies_needing_review"], "replies to review"),
    ]
    grid = "".join(f'<div class="stat"><b>{value}</b><span class="muted">{escape(label)}</span></div>' for value, label in stats)
    theme_rows = ""
    for name, counts in sorted(summary["themes"].items(), key=lambda pair: -pair[1]["negative"]):
        label = taxonomy["themes"][name].get("label", name)
        theme_rows += f'<div class="row"><span>{escape(label)}</span>{_bar(counts)}<span class="muted">{counts["negative"]} of {sum(counts.values())} neg</span></div>'
    language_rows = ""
    for code in summary["languages"]:
        group = [record for record in records if not record["error"] and record["language"] == code]
        counts = {label: sum(record["sentiment"] == label for record in group) for label in SENTIMENTS}
        language_rows += f'<div class="row"><span>{escape(LANGUAGE_NAMES.get(code, code))}</span>{_bar(counts)}<span class="muted">{len(group)}</span></div>'
    order = {"negative": 0, "neutral": 1, "positive": 2, None: 3}
    ranked = sorted(records, key=lambda record: (order[record["sentiment"]], record["score"] if record["score"] is not None else 0))
    items = "".join(_item_html(record, taxonomy) for record in ranked)
    calls = ", ".join(f"{model} {count}" for model, count in sorted(usage.get("calls", {}).items())) or "none"
    cached = sum(usage.get("cache_hits", {}).values())
    channels = ", ".join(f"{count} {name}" for name, count in summary["channels"].items())
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head><body><main>
<h1>{escape(title)}</h1>
<p class="muted">{summary["analysed"]} analysed ({escape(channels)}), {summary["errors"]} failed. Bodhan calls this run: {escape(calls)}; {cached} answered from cache.</p>
<div class="grid">{grid}</div>
<div class="cols"><section><h2>What people talk about</h2><div class="panel">{theme_rows}
<p class="muted">{summary["no_theme"]} pieces matched no theme.</p></div></section>
<section><h2>Sentiment by language</h2><div class="panel">{language_rows}</div></section></div>
<h2>Every piece of feedback, worst first</h2>{items}
</main></body></html>"""


def write_all(out_dir: Path, records: list[dict], taxonomy: dict, usage: dict, title: str = "Customer feedback report") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarise(records, taxonomy)
    write_json(out_dir / "report.json", records, summary, usage)
    write_csv(out_dir / "report.csv", records)
    (out_dir / "report.html").write_text(render_html(records, summary, taxonomy, usage, title), encoding="utf-8")
    return summary
