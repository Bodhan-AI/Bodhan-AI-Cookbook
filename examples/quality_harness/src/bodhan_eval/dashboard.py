"""Static, self-contained HTML dashboard -- "the proofreader's desk".

One file, no server, no CDN, no network: inline CSS + inline JS + inline JSON data. It opens by
double-click and works offline. Generated from the report bundle (dashboard_data.json, or
items.jsonl + summary.json if that is absent). Deterministic: same report -> byte-identical HTML
(sorted keys, no timestamps in the body). All item text is escaped and rendered with textContent /
an escaper -- never eval, never innerHTML with untrusted strings.

    python -m bodhan_eval.dashboard --report ./report      # writes ./report/dashboard.html
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent / "dashboard_assets"


def _read(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


def render(data: dict) -> str:
    """Return a complete self-contained HTML document for the given dashboard data dict."""
    css = _read("dashboard.css")
    js = _read("dashboard.js")
    tmpl = _read("dashboard.html.tmpl")
    # embed data safely: sorted keys (deterministic), and neutralize "</" so a "</script>" inside
    # any string cannot close the data block.
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    html = (tmpl
            .replace("/*__CSS__*/", css)
            .replace("/*__JS__*/", js)
            .replace("__DATA__", payload))
    return html


def build_from_report(report_dir: str) -> Path:
    """Load the report bundle and write <report_dir>/dashboard.html."""
    rd = Path(report_dir)
    data_path = rd / "dashboard_data.json"
    if data_path.exists():
        data = json.loads(data_path.read_text(encoding="utf-8"))
    else:
        data = _reconstruct_from_report(rd)
    html = render(data)
    out = rd / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out


def _reconstruct_from_report(rd: Path) -> dict:
    """Fallback bundle when dashboard_data.json is absent (no source/translation text available)."""
    items = []
    items_path = rd / "items.jsonl"
    if items_path.exists():
        for line in items_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                o = json.loads(line)
                items.append({"id": o["id"], "lang": o["lang"], "status": o["status"],
                              "source": "", "translation": "", "reference": "",
                              "flags": o.get("flags", []),
                              "uncertain_categories": o.get("details", {}).get("uncertain_categories", []),
                              "judge_verdict": None, "checker_disagreement": False,
                              "signal": None, "cost_inr": o.get("cost_inr", 0)})
    summary = {}
    if (rd / "summary.json").exists():
        summary = json.loads((rd / "summary.json").read_text(encoding="utf-8"))
    calibration = {}
    if (rd / "calibration.json").exists():
        calibration = json.loads((rd / "calibration.json").read_text(encoding="utf-8")).get("metrics", {})
    return {"summary": summary, "items": items, "calibration": calibration, "diff": None}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bodhan_eval.dashboard")
    p.add_argument("--report", required=True, help="path to a report directory")
    args = p.parse_args(argv)
    out = build_from_report(args.report)
    print(f"Dashboard written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
