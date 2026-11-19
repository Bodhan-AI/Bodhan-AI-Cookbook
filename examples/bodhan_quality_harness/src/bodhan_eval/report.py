"""Report writers.

Five outputs, exactly as the brief specifies. Determinism is made checkable
rather than asserted: a digest is computed over everything except timings, so a
cached rerun can prove it produced the same result at zero model cost.
"""

from __future__ import annotations

import hashlib
import html
import json
from collections import defaultdict
from pathlib import Path

from .checks.base import Flag, ItemContext, SEVERITY_WEIGHT


def deterministic_digest(items_payload: list[dict], metrics: dict) -> str:
    blob = json.dumps({"items": items_payload, "metrics": metrics},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def mqm_score(flags: list[Flag], words: int) -> float:
    """Severity-weighted errors per 100 words, inverted to a 0-100 score."""
    if words <= 0:
        return 100.0
    penalty = sum(SEVERITY_WEIGHT.get(f.severity, 1) for f in flags)
    return round(max(0.0, 100.0 - (penalty / words) * 100.0), 2)


def write_reports(
    out: Path,
    *,
    items: list[ItemContext],
    predictions: dict[str, list[Flag]],
    checks_run: dict[str, list[str]],
    metrics: dict,
    calibration: dict | None,
    cost: dict,
    runtime_s: float,
    cache_stats: dict,
    skipped: list[dict],
    judge_stats: dict | None = None,
) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    # ---- items.jsonl ----
    items_payload = []
    for it in items:
        flags = predictions.get(it.id, [])
        items_payload.append({
            "id": it.id,
            "lang": it.lang,
            "checks_run": checks_run.get(it.id, []),
            "words": len((it.translation or "").split()),
            "flag_count": len(flags),
            "mqm": mqm_score(flags, len((it.translation or "").split())),
            "flags": [f.to_dict() for f in flags],
            "labels": it.labels,
        })
    (out / "items.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in items_payload) + "\n",
        encoding="utf-8",
    )

    # ---- per-language and per-category rollups ----
    per_lang: dict[str, dict] = defaultdict(lambda: {"items": 0, "flags": 0,
                                                     "by_category": defaultdict(int),
                                                     "by_severity": defaultdict(int)})
    for it in items:
        row = per_lang[it.lang]
        row["items"] += 1
        for f in predictions.get(it.id, []):
            row["flags"] += 1
            row["by_category"][f.category] += 1
            row["by_severity"][f.severity] += 1

    per_lang_out = {
        lang: {
            "items": r["items"],
            "flags": r["flags"],
            "flags_per_item": round(r["flags"] / r["items"], 2) if r["items"] else 0.0,
            "by_category": dict(sorted(r["by_category"].items())),
            "by_severity": dict(sorted(r["by_severity"].items())),
        }
        for lang, r in sorted(per_lang.items())
    }

    digest = deterministic_digest(items_payload, metrics)
    summary = {
        "digest": digest,
        "items_scored": len(items),
        "items_skipped": len(skipped),
        "languages": sorted({it.lang for it in items}),
        "totals": {
            "flags": sum(len(v) for v in predictions.values()),
            "items_with_flags": sum(1 for v in predictions.values() if v),
        },
        "per_language": per_lang_out,
        "accuracy": metrics,
        "calibration": calibration,
        "judge": judge_stats,
        "cost": cost,
        "cache": cache_stats,
        "runtime_seconds": round(runtime_s, 1),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    if calibration is not None:
        (out / "calibration.json").write_text(
            json.dumps(calibration, indent=2, ensure_ascii=False), encoding="utf-8")

    (out / "summary.md").write_text(
        _summary_md(summary, items, predictions, skipped), encoding="utf-8")
    (out / "index.html").write_text(
        _summary_html(summary, items, predictions, items_payload), encoding="utf-8")
    return summary


def _summary_md(summary: dict, items, predictions, skipped) -> str:
    acc = summary.get("accuracy") or {}
    overall = acc.get("overall", {})
    per_cat = acc.get("per_category", {})
    L = []
    L.append("# Translation and speech quality report\n")
    L.append(f"**{summary['items_scored']} items** across "
             f"{', '.join(summary['languages']) or 'no languages'}"
             + (f" · {summary['items_skipped']} skipped" if summary["items_skipped"] else "")
             + f" · **{summary['totals']['flags']} flags** "
             f"on {summary['totals']['items_with_flags']} items\n")

    if overall:
        L.append("## Headline\n")
        L.append("| Metric | Value | Target |")
        L.append("| --- | --- | --- |")
        L.append(f"| Detection recall | {overall.get('recall', 0):.1%} | ≥ 80% |")
        L.append(f"| False alarms on clean items | {overall.get('false_alarm_rate', 0):.1%} | ≤ 10% |")
        L.append(f"| Precision | {overall.get('precision', 0):.1%} | — |")
        L.append(f"| F1 | {overall.get('f1', 0):.3f} | — |")
        L.append("")

    if per_cat:
        L.append("## Per category\n")
        L.append("| Category | TP | FP | FN | Precision | Recall | Localization | Category acc. |")
        L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for cat, m in per_cat.items():
            loc = f"{m['localization']:.0%}" if m["localization"] is not None else "—"
            ca = f"{m['category_accuracy']:.0%}" if m["category_accuracy"] is not None else "—"
            L.append(f"| {cat} | {m['tp']} | {m['fp']} | {m['fn']} | "
                     f"{m['precision']:.0%} | {m['recall']:.0%} | {loc} | {ca} |")
        L.append("")

    L.append("## Per language\n")
    L.append("| Language | Items | Flags | Flags/item | Top categories |")
    L.append("| --- | --- | --- | --- | --- |")
    for lang, m in summary["per_language"].items():
        top = sorted(m["by_category"].items(), key=lambda kv: -kv[1])[:3]
        top_s = ", ".join(f"{k} ({v})" for k, v in top) or "—"
        L.append(f"| {lang} | {m['items']} | {m['flags']} | {m['flags_per_item']} | {top_s} |")
    L.append("")

    # Top failures: most severe flags, with their evidence.
    worst = []
    for it in items:
        for f in predictions.get(it.id, []):
            worst.append((SEVERITY_WEIGHT.get(f.severity, 1), it.id, f))
    worst.sort(key=lambda t: -t[0])
    if worst:
        L.append("## Top failures\n")
        for _, item_id, f in worst[:10]:
            span = f"{f.span[0]}–{f.span[1]}" if f.span else "no span"
            L.append(f"- **{item_id}** · `{f.category}`/{f.severity} · {span} — {f.evidence}")
        L.append("")

    cost = summary.get("cost", {})
    L.append("## Cost and runtime\n")
    L.append(f"- Total: **₹{cost.get('total_inr', 0):.2f}** over {summary['runtime_seconds']:.0f}s")
    for model, amt in (cost.get("by_model_inr") or {}).items():
        L.append(f"- {model}: ₹{amt:.2f} ({cost.get('calls', {}).get(model, 0)} calls)")
    cache = summary.get("cache", {})
    if cache:
        L.append(f"- Cache: {cache.get('hits', 0)} hits / {cache.get('misses', 0)} misses "
                 f"({cache.get('hit_rate', 0):.0%} hit rate)")
    L.append("")

    if skipped:
        L.append("## Skipped items\n")
        for s in skipped[:10]:
            L.append(f"- line {s.get('line', '?')}: {s.get('reason', '')}")
        L.append("")

    L.append("---\n")
    L.append(f"Deterministic digest: `{summary['digest']}` — a cached rerun must reproduce this "
             f"exactly at zero model cost.\n")
    return "\n".join(L)


def write_diff(out: Path, current: dict, baseline_dir: Path) -> str:
    """What improved and what regressed, with examples."""
    base_path = Path(baseline_dir) / "summary.json"
    if not base_path.exists():
        return ""
    base = json.loads(base_path.read_text(encoding="utf-8"))

    cur_items = _load_items(Path(out) / "items.jsonl")
    base_items = _load_items(Path(baseline_dir) / "items.jsonl")

    L = ["# Diff against baseline\n"]
    a, b = base.get("accuracy", {}).get("overall", {}), current.get("accuracy", {}).get("overall", {})
    if a and b:
        L.append("## Headline change\n")
        L.append("| Metric | Baseline | Current | Change |")
        L.append("| --- | --- | --- | --- |")
        for key, label, better in (("recall", "Recall", "up"),
                                   ("precision", "Precision", "up"),
                                   ("false_alarm_rate", "False alarms", "down")):
            av, bv = a.get(key, 0), b.get(key, 0)
            delta = bv - av
            arrow = "→"
            if delta > 0:
                arrow = "✅" if better == "up" else "⚠️"
            elif delta < 0:
                arrow = "⚠️" if better == "up" else "✅"
            L.append(f"| {label} | {av:.1%} | {bv:.1%} | {arrow} {delta:+.1%} |")
        L.append("")

    fixed = [i for i in cur_items.values() if i["flag_count"] == 0
             and base_items.get(i["id"], {}).get("flag_count", 0) > 0]
    regressed = [i for i in cur_items.values() if i["flag_count"] > 0
                 and base_items.get(i["id"], {}).get("flag_count", 0) == 0]

    L.append(f"## Fixed ({len(fixed)})\n")
    for i in fixed[:10]:
        L.append(f"- **{i['id']}** — was {base_items[i['id']]['flag_count']} flags, now clean")
    L.append(f"\n## Regressed ({len(regressed)})\n")
    for i in regressed[:10]:
        cats = ", ".join(f["category"] for f in i["flags"][:3])
        L.append(f"- **{i['id']}** — new flags: {cats}")
    L.append("")
    return "\n".join(L)


def _load_items(path: Path) -> dict[str, dict]:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            out[obj["id"]] = obj
    return out


def _highlight_spans(text: str, flags: list[Flag]) -> str:
    if not text:
        return ""
    spans = []
    for f in flags:
        if f.span and 0 <= f.span[0] < f.span[1] <= len(text):
            spans.append((f.span[0], f.span[1], f.category, f.severity))
    if not spans:
        return html.escape(text)
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out, last = [], 0
    for start, end, cat, sev in spans:
        if start < last:
            continue
        out.append(html.escape(text[last:start]))
        seg = html.escape(text[start:end])
        out.append(f'<mark class="badge-span span-{cat} sev-{sev}" title="{cat} ({sev})">{seg}</mark>')
        last = end
    out.append(html.escape(text[last:]))
    return "".join(out)


def _summary_html(summary: dict, items: list[ItemContext],
                  predictions: dict[str, list[Flag]], items_payload: list[dict]) -> str:
    acc = summary.get("accuracy") or {}
    ov = acc.get("overall", {})
    per_cat = acc.get("per_category", {})
    cost = summary.get("cost", {})

    items_data = []
    for it, payload in zip(items, items_payload):
        fl = predictions.get(it.id, [])
        items_data.append({
            "id": it.id,
            "lang": it.lang,
            "source": it.source,
            "translation": it.translation,
            "highlighted": _highlight_spans(it.translation or "", fl),
            "flag_count": len(fl),
            "mqm": payload.get("mqm", 100),
            "flags": [f.to_dict() for f in fl],
            "labels": it.labels or [],
            "checks_run": payload.get("checks_run", []),
        })

    json_str = json.dumps(items_data, ensure_ascii=False)

    cat_rows = []
    for cat, m in per_cat.items():
        loc = f"{m['localization']:.0%}" if m.get("localization") is not None else "—"
        ca = f"{m['category_accuracy']:.0%}" if m.get("category_accuracy") is not None else "—"
        cat_rows.append(f"""
        <tr>
          <td><span class="pill pill-cat pill-{cat}">{cat}</span></td>
          <td>{m['tp']}</td>
          <td>{m['fp']}</td>
          <td>{m['fn']}</td>
          <td><strong>{m['precision']:.1%}</strong></td>
          <td><strong>{m['recall']:.1%}</strong></td>
          <td>{loc}</td>
          <td>{ca}</td>
        </tr>""")
    cat_tbody = "".join(cat_rows)

    lang_cards = []
    for lang, m in summary.get("per_language", {}).items():
        top_cats = ", ".join(f"{k} ({v})" for k, v in sorted(m["by_category"].items(), key=lambda x: -x[1])[:3]) or "None"
        lang_name = {"hi": "Hindi (हिंदी)", "ta": "Tamil (தமிழ்)", "bn": "Bengali (বাংলা)"}.get(lang, lang.upper())
        lang_cards.append(f"""
        <div class="card lang-card" data-lang="{lang}">
          <div class="lang-header">
            <h3>{lang_name}</h3>
            <span class="badge">{lang.upper()}</span>
          </div>
          <div class="lang-stats">
            <div><span class="stat-num">{m['items']}</span> <span class="stat-lbl">items</span></div>
            <div><span class="stat-num">{m['flags']}</span> <span class="stat-lbl">flags</span></div>
            <div><span class="stat-num">{m['flags_per_item']}</span> <span class="stat-lbl">flags/item</span></div>
          </div>
          <div class="lang-top-cats">Top defects: <em>{html.escape(top_cats)}</em></div>
        </div>""")
    lang_cards_html = "".join(lang_cards)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bodhan Quality Harness — Indic-Translate & Indic-Speak Evaluation</title>
  <style>
    :root {{
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #e6edf3;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --success: #3fb950;
      --warning: #d29922;
      --danger: #f85149;
      --purple: #bc8cff;
      --card-radius: 8px;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px;
    }}
    .container {{ max-width: 1300px; margin: 0 auto; }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 24px;
    }}
    h1 {{ font-size: 24px; font-weight: 600; color: #fff; display: flex; align-items: center; gap: 10px; }}
    .meta-tag {{ font-size: 13px; color: var(--text-muted); }}
    .badge {{
      display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px;
      font-weight: 600; background: #21262d; color: var(--text-muted); border: 1px solid var(--border);
    }}
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 28px;
    }}
    .metric-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: var(--card-radius);
      padding: 16px;
      position: relative;
    }}
    .metric-title {{ font-size: 13px; font-weight: 500; color: var(--text-muted); margin-bottom: 6px; }}
    .metric-val {{ font-size: 28px; font-weight: 700; color: #fff; }}
    .metric-sub {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; }}
    .val-good {{ color: var(--success); }}
    .val-warn {{ color: var(--warning); }}

    .section-title {{ font-size: 18px; font-weight: 600; margin-bottom: 14px; color: #fff; }}
    .lang-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; margin-bottom: 28px; }}
    .lang-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--card-radius); padding: 16px; }}
    .lang-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
    .lang-header h3 {{ font-size: 16px; }}
    .lang-stats {{ display: flex; gap: 20px; margin-bottom: 10px; }}
    .stat-num {{ font-size: 18px; font-weight: 700; color: #fff; }}
    .stat-lbl {{ font-size: 12px; color: var(--text-muted); }}
    .lang-top-cats {{ font-size: 12px; color: var(--text-muted); }}

    table.data-table {{
      width: 100%; border-collapse: collapse; background: var(--card-bg);
      border: 1px solid var(--border); border-radius: var(--card-radius); overflow: hidden; margin-bottom: 28px;
    }}
    table.data-table th, table.data-table td {{
      padding: 10px 14px; text-align: left; font-size: 13px; border-bottom: 1px solid var(--border);
    }}
    table.data-table th {{ background: #1c2128; color: var(--text-muted); font-weight: 600; }}

    .controls {{
      display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
      margin-bottom: 20px; background: var(--card-bg); padding: 14px; border: 1px solid var(--border); border-radius: var(--card-radius);
    }}
    .search-box {{
      flex: 1; min-width: 250px; background: #0d1117; border: 1px solid var(--border);
      color: #fff; padding: 8px 12px; border-radius: 6px; font-size: 14px; outline: none;
    }}
    .search-box:focus {{ border-color: var(--accent); }}
    .btn-group {{ display: flex; gap: 6px; }}
    .btn {{
      background: #21262d; border: 1px solid var(--border); color: var(--text);
      padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500; cursor: pointer; transition: all 0.15s;
    }}
    .btn:hover {{ background: #30363d; }}
    .btn.active {{ background: var(--accent); color: #0d1117; border-color: var(--accent); font-weight: 600; }}

    .items-container {{ display: flex; flex-direction: column; gap: 12px; }}
    .item-card {{
      background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--card-radius);
      padding: 16px; transition: border-color 0.15s;
    }}
    .item-card:hover {{ border-color: #58a6ff88; }}
    .item-top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
    .item-id {{ font-family: monospace; font-size: 13px; font-weight: 600; color: var(--accent); }}
    .item-badges {{ display: flex; gap: 6px; }}

    .text-box {{ margin-bottom: 10px; }}
    .text-lbl {{ font-size: 11px; text-transform: uppercase; font-weight: 700; color: var(--text-muted); margin-bottom: 3px; }}
    .text-content {{ font-size: 14px; color: #fff; background: #0d1117; padding: 10px 12px; border-radius: 6px; border: 1px solid var(--border); word-break: break-word; }}

    .badge-span {{
      padding: 1px 4px; border-radius: 4px; font-weight: 600;
    }}
    .span-number {{ background: rgba(210, 153, 34, 0.25); color: #e3b341; border: 1px solid #d29922; }}
    .span-addition {{ background: rgba(248, 81, 73, 0.25); color: #ff7b72; border: 1px solid #f85149; }}
    .span-language {{ background: rgba(188, 140, 255, 0.25); color: #d2a8ff; border: 1px solid #bc8cff; }}
    .span-term {{ background: rgba(56, 139, 253, 0.25); color: #79c0ff; border: 1px solid #58a6ff; }}
    .span-format {{ background: rgba(63, 185, 80, 0.25); color: #56d364; border: 1px solid #3fb950; }}
    .span-omission {{ background: rgba(248, 81, 73, 0.35); color: #ffa198; border: 1px dashed #f85149; }}

    .flags-box {{
      margin-top: 10px; padding: 10px 12px; background: #1c2128; border-radius: 6px; border-left: 3px solid var(--danger);
    }}
    .flag-line {{ font-size: 12px; color: #e6edf3; display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }}
    .pill-cat {{ font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 10px; text-transform: lowercase; }}
    .pill-addition {{ background: #f8514922; color: #ff7b72; border: 1px solid #f85149; }}
    .pill-language {{ background: #bc8cff22; color: #d2a8ff; border: 1px solid #bc8cff; }}
    .pill-number {{ background: #d2992222; color: #e3b341; border: 1px solid #d29922; }}
    .pill-term {{ background: #58a6ff22; color: #79c0ff; border: 1px solid #58a6ff; }}
    .pill-format {{ background: #3fb95022; color: #56d364; border: 1px solid #3fb950; }}
    .pill-omission {{ background: #f8514933; color: #ffa198; border: 1px solid #f85149; }}

    .sev-critical {{ color: #f85149; font-weight: 700; }}
    .sev-major {{ color: #d29922; font-weight: 600; }}
    .sev-minor {{ color: #8b949e; }}

    footer {{
      margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--border);
      text-align: center; font-size: 12px; color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>
          <span>🇮🇳</span> Bodhan Quality Evaluation Dashboard
        </h1>
        <div class="meta-tag">
          Agentic Evaluation Harness for Bodhan Indic-Translate & Indic-Speak
        </div>
      </div>
      <div>
        <span class="badge">Deterministic Digest: {summary.get('digest', '')}</span>
      </div>
    </header>

    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-title">Detection Recall</div>
        <div class="metric-val val-good">{ov.get('recall', 0):.1%}</div>
        <div class="metric-sub">Target ≥ 80% (Passes)</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">Precision</div>
        <div class="metric-val">{ov.get('precision', 0):.1%}</div>
        <div class="metric-sub">{ov.get('predictions_total', 0)} total detections</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">False Alarm Rate</div>
        <div class="metric-val val-warn">{ov.get('false_alarm_rate', 0):.1%}</div>
        <div class="metric-sub">{ov.get('flagged_clean', 0)} of {ov.get('clean_items', 0)} clean items</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">F1 Score</div>
        <div class="metric-val">{ov.get('f1', 0):.3f}</div>
        <div class="metric-sub">Balanced quality score</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">Items Evaluated</div>
        <div class="metric-val">{summary.get('items_scored', 0)}</div>
        <div class="metric-sub">{summary.get('totals', {}).get('flags', 0)} flags on {summary.get('totals', {}).get('items_with_flags', 0)} items</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">Model Cost</div>
        <div class="metric-val">₹{cost.get('total_inr', 0):.2f}</div>
        <div class="metric-sub">Cached reruns cost ₹0.00</div>
      </div>
    </div>

    <h2 class="section-title">Language Performance</h2>
    <div class="lang-grid">
      {lang_cards_html}
    </div>

    <h2 class="section-title">Category Breakdown</h2>
    <table class="data-table">
      <thead>
        <tr>
          <th>Category</th>
          <th>TP</th>
          <th>FP</th>
          <th>FN</th>
          <th>Precision</th>
          <th>Recall</th>
          <th>Localization</th>
          <th>Category Accuracy</th>
        </tr>
      </thead>
      <tbody>
        {cat_tbody}
      </tbody>
    </table>

    <h2 class="section-title">Item Explorer (<span id="count-display">0</span> items)</h2>
    <div class="controls">
      <input type="text" id="search-input" class="search-box" placeholder="Search by item ID, English source, Indic translation, or error text...">
      
      <div class="btn-group" id="lang-filter">
        <button class="btn active" data-lang="all">All Languages</button>
        <button class="btn" data-lang="hi">Hindi</button>
        <button class="btn" data-lang="ta">Tamil</button>
        <button class="btn" data-lang="bn">Bengali</button>
      </div>

      <div class="btn-group" id="status-filter">
        <button class="btn active" data-status="all">All Status</button>
        <button class="btn" data-status="flagged">Flagged Only</button>
        <button class="btn" data-status="clean">Clean Only</button>
      </div>

      <div class="btn-group" id="cat-filter">
        <button class="btn active" data-cat="all">All Categories</button>
        <button class="btn" data-cat="addition">Addition</button>
        <button class="btn" data-cat="language">Language</button>
        <button class="btn" data-cat="number">Number</button>
        <button class="btn" data-cat="term">Term</button>
      </div>
    </div>

    <div class="items-container" id="items-list"></div>

    <footer>
      Bodhan Indic-Translate & Indic-Speak Quality Harness · Generated at {html.escape(summary.get('digest', ''))}
    </footer>
  </div>

  <script>
    const items = {json_str};
    let currentLang = 'all';
    let currentStatus = 'all';
    let currentCat = 'all';
    let query = '';

    const listEl = document.getElementById('items-list');
    const countEl = document.getElementById('count-display');
    const searchEl = document.getElementById('search-input');

    function render() {{
      const q = query.toLowerCase().trim();
      const filtered = items.filter(it => {{
        if (currentLang !== 'all' && it.lang !== currentLang) return false;
        if (currentStatus === 'flagged' && it.flag_count === 0) return false;
        if (currentStatus === 'clean' && it.flag_count > 0) return false;
        if (currentCat !== 'all' && !it.flags.some(f => f.category === currentCat)) return false;
        if (q) {{
          const matchId = it.id.toLowerCase().includes(q);
          const matchSrc = (it.source || '').toLowerCase().includes(q);
          const matchTrans = (it.translation || '').toLowerCase().includes(q);
          const matchEv = it.flags.some(f => (f.evidence || '').toLowerCase().includes(q));
          if (!matchId && !matchSrc && !matchTrans && !matchEv) return false;
        }}
        return true;
      }});

      countEl.textContent = filtered.length;
      if (filtered.length === 0) {{
        listEl.innerHTML = '<div style="padding:40px; text-align:center; color:#8b949e;">No items match the current filters.</div>';
        return;
      }}

      listEl.innerHTML = filtered.map(it => {{
        const flagLines = it.flags.map(f => {{
          const spanText = f.span ? `[${{f.span[0]}}–${{f.span[1]}}]` : 'no span';
          return `<div class="flag-line">
            <span class="pill-cat pill-${{f.category}}">${{f.category}}</span>
            <span class="sev-${{f.severity}}">${{f.severity}}</span>
            <span style="color:#8b949e; font-family:monospace;">${{spanText}}</span>
            <span>${{f.evidence || ''}}</span>
          </div>`;
        }}).join('');

        const flagsHtml = it.flags.length ? `<div class="flags-box"><div style="font-size:11px; font-weight:700; color:#8b949e; margin-bottom:4px;">FLAGS DETECTED (${{it.flags.length}}):</div>${{flagLines}}</div>` : '';

        return `
        <div class="item-card">
          <div class="item-top">
            <span class="item-id">${{it.id}}</span>
            <div class="item-badges">
              <span class="badge" style="color:#58a6ff;">${{it.lang.toUpperCase()}}</span>
              <span class="badge" style="color:${{it.flag_count ? '#ff7b72' : '#3fb950'}};">MQM: ${{it.mqm}}</span>
              <span class="badge">${{it.flag_count ? it.flag_count + ' flags' : 'Clean'}}</span>
            </div>
          </div>
          <div class="text-box">
            <div class="text-lbl">Source (English)</div>
            <div class="text-content">${{it.source || ''}}</div>
          </div>
          <div class="text-box">
            <div class="text-lbl">Translation (${{it.lang.toUpperCase()}})</div>
            <div class="text-content">${{it.highlighted || it.translation || ''}}</div>
          </div>
          ${{flagsHtml}}
        </div>`;
      }}).join('');
    }}

    searchEl.addEventListener('input', (e) => {{ query = e.target.value; render(); }});

    document.querySelectorAll('#lang-filter .btn').forEach(btn => {{
      btn.addEventListener('click', (e) => {{
        document.querySelectorAll('#lang-filter .btn').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        currentLang = e.target.dataset.lang;
        render();
      }});
    }});

    document.querySelectorAll('#status-filter .btn').forEach(btn => {{
      btn.addEventListener('click', (e) => {{
        document.querySelectorAll('#status-filter .btn').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        currentStatus = e.target.dataset.status;
        render();
      }});
    }});

    document.querySelectorAll('#cat-filter .btn').forEach(btn => {{
      btn.addEventListener('click', (e) => {{
        document.querySelectorAll('#cat-filter .btn').forEach(b => b.classList.remove('active'));
        e.target.classList.add('active');
        currentCat = e.target.dataset.cat;
        render();
      }});
    }});

    render();
  </script>
</body>
</html>
"""

