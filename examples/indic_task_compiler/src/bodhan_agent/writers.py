"""Output writers: result.md, report.json, trace.jsonl, and self-contained static trace.html."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from bodhan_agent.models import RunReport, TraceEvent


def write_trace_jsonl(path: Path, events: list[TraceEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(ev.model_dump(), ensure_ascii=False) for ev in events]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_report_json(path: Path, report: RunReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_result_md(
    path: Path,
    *,
    report: RunReport,
    final_answer: str,
    probe_summary: list[dict[str, Any]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        f"# Task Result ({report.status.upper()})",
        "",
        f"**Task:** {report.task}",
        f"**Status:** `{report.status}`",
        f"**Total Bodhan Cost:** ₹{report.total_cost_inr:.4f} / ₹{report.max_cost_inr:.2f}",
        f"**Steps Used:** {report.steps_used} / {report.max_steps} | **Runtime:** {report.total_time_s:.2f}s | **Cache Hits:** {report.cache_hits}",
        "",
    ]

    if report.reason:
        lines += [
            "## Outcome Details",
            "",
            report.reason,
            "",
        ]

    if final_answer:
        lines += [
            "## Answer & Summary",
            "",
            final_answer.strip(),
            "",
        ]

    if probe_summary:
        lines += [
            "## Detected Input Languages (Probes)",
            "",
            "| File | Detected Language | Confidence | Ambiguous | Method |",
            "| --- | --- | --- | --- | --- |",
        ]
        for p in probe_summary:
            fname = Path(str(p.get("path", ""))).name
            lines.append(
                f"| `{fname}` | `{p.get('language') or 'unknown'}` | {p.get('confidence', 0.0):.2f} | "
                f"{p.get('ambiguous')} | `{p.get('method', '')}` |"
            )
        lines.append("")

    if report.artifacts:
        lines += ["## Artifacts Produced", ""]
        has_audio = False
        for art in report.artifacts:
            rel = art.get("relative_path") or Path(str(art.get("path", ""))).name
            kind = art.get("kind", "file")
            if kind == "audio":
                has_audio = True
            lines.append(f"- **{art.get('deliverable_id', 'output')}** (`{kind}`): [{rel}]({rel})")
        lines.append("")
        if has_audio:
            lines += [
                "> **AI-Generated Audio Disclosure:** All speech audio artifacts were synthesized by Bodhan.ai's "
                "`indic-speak` model using fixed library voices (no voice cloning) and tagged as AI-generated in file metadata.",
                "",
            ]
    else:
        lines += ["## Artifacts Produced", "", "_No deliverable artifacts were produced._", ""]

    if report.assumptions:
        lines += ["## Assumptions", ""]
        for a in report.assumptions:
            lines.append(f"- {a}")
        lines.append("")

    if report.verification:
        lines += [
            "## Verification Summary",
            "",
            "| Deliverable | Kind | Passed | Checks |",
            "| --- | --- | --- | --- |",
        ]
        for v in report.verification:
            chk_summary = "; ".join(
                f"{'PASS' if c.passed else 'FAIL'} {c.name} ({c.detail})" for c in v.checks
            )
            lines.append(f"| `{v.deliverable_id}` | `{v.kind}` | {'PASS' if v.passed else 'FAIL'} | {chk_summary} |")
        lines.append("")

    lines += [
        "## Trace & Report",
        "",
        "- Structured report: [report.json](report.json)",
        "- Step-by-step trace: [trace.jsonl](trace.jsonl)",
        "- Visual trace viewer: [trace.html](trace.html)",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


def write_trace_html(path: Path, report: RunReport, events: list[TraceEvent]) -> None:
    """Render a self-contained static HTML trace viewer from trace.jsonl + report.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    status_color = {
        "success": "#16a34a",
        "partial": "#d97706",
        "failed": "#dc2626",
        "infeasible": "#2563eb",
    }.get(report.status, "#475569")

    rows_html: list[str] = []
    for ev in events:
        badge_cls = "ok" if ev.ok else "err"
        cache_tag = '<span class="pill cache">CACHE</span>' if ev.cache_hit else ""
        args_pretty = html.escape(json.dumps(ev.args_sanitized, ensure_ascii=False, indent=2))
        meta_pretty = html.escape(json.dumps(ev.result_meta, ensure_ascii=False, indent=2))
        rows_html.append(
            f"""<tr>
              <td>{ev.step_index}</td>
              <td><span class="pill epoch">{html.escape(ev.epoch)}</span></td>
              <td><code>{html.escape(ev.tool)}</code> {cache_tag}<div class="sub">{html.escape(ev.plan_excerpt)}</div></td>
              <td><span class="pill {badge_cls}">{'OK' if ev.ok else 'ERR'}</span></td>
              <td>{ev.latency_ms} ms</td>
              <td>₹{ev.cost_inr:.4f}</td>
              <td>
                <details><summary>args / meta</summary>
                  <div class="label">Args (handles only)</div>
                  <pre>{args_pretty}</pre>
                  <div class="label">Result Meta (CaMeL-sanitized)</div>
                  <pre>{meta_pretty}</pre>
                </details>
              </td>
            </tr>"""
        )

    cost_items = "".join(
        f"<li><code>{html.escape(m)}</code>: ₹{c:.4f}</li>" for m, c in sorted(report.cost_by_model.items())
    ) or "<li>₹0.0000 (no paid Bodhan calls)</li>"

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Bodhan Agent Trace — {html.escape(report.status.upper())}</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }}
  .wrap {{ max-width: 1120px; margin: 0 auto; }}
  h1 {{ margin: 0 0 8px; font-size: 22px; }}
  .task {{ color: #94a3b8; margin-bottom: 18px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }}
  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 14px; }}
  .card .k {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: .04em; }}
  .card .v {{ font-size: 20px; font-weight: 700; margin-top: 4px; }}
  .badge-status {{ display: inline-block; padding: 3px 10px; border-radius: 999px; background: {status_color}; color: #fff; font-size: 12px; font-weight: 700; vertical-align: middle; margin-left: 8px; }}
  .camel {{ background: #064e3b; color: #a7f3d0; border: 1px solid #10b981; border-radius: 8px; padding: 10px 14px; margin-bottom: 18px; font-size: 13px; }}
  table {{ width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 10px; overflow: hidden; }}
  th, td {{ padding: 10px 12px; border-bottom: 1px solid #334155; text-align: left; vertical-align: top; font-size: 13px; }}
  th {{ background: #0b1220; color: #94a3b8; font-weight: 600; }}
  .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }}
  .pill.epoch {{ background: #334155; color: #e2e8f0; }}
  .pill.ok {{ background: #14532d; color: #86efac; }}
  .pill.err {{ background: #7f1d1d; color: #fecaca; }}
  .pill.cache {{ background: #1e3a8a; color: #bfdbfe; margin-left: 4px; }}
  .sub {{ color: #94a3b8; font-size: 12px; margin-top: 3px; }}
  pre {{ background: #0b1220; padding: 8px; border-radius: 6px; overflow-x: auto; font-size: 11px; max-height: 220px; }}
  .label {{ color: #94a3b8; font-size: 11px; margin-top: 6px; }}
  ul {{ margin: 6px 0 0 18px; padding: 0; font-size: 13px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Indic Task Compiler Trace <span class="badge-status">{html.escape(report.status.upper())}</span></h1>
  <div class="task">{html.escape(report.task)}</div>
  <div class="camel">
    <strong>CaMeL Isolation Active:</strong> The Privileged Planner (P-LLM) saw only FileFacts metadata, typed probe enums, and <code>$step.field</code> artifact handles. Untrusted file/OCR/transcript/translation content was stripped before entering planner context.
  </div>
  <div class="cards">
    <div class="card"><div class="k">Total Spend</div><div class="v">₹{report.total_cost_inr:.4f} / ₹{report.max_cost_inr:.2f}</div></div>
    <div class="card"><div class="k">Steps</div><div class="v">{report.steps_used} / {report.max_steps}</div></div>
    <div class="card"><div class="k">Runtime</div><div class="v">{report.total_time_s:.2f} s</div></div>
    <div class="card"><div class="k">Cache Hits</div><div class="v">{report.cache_hits}</div></div>
    <div class="card"><div class="k">Cost by Model</div><ul>{cost_items}</ul></div>
  </div>
  <table>
    <thead>
      <tr><th>#</th><th>Epoch</th><th>Tool / Step</th><th>Status</th><th>Latency</th><th>Cost</th><th>Payload (Handles &amp; Meta)</th></tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
</div>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
