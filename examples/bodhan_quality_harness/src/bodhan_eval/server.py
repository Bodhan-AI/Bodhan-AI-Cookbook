"""Interactive Web Server & Live Evaluation API for Bodhan Quality Harness.

Provides both an interactive single-page web dashboard and REST APIs to:
1. Live-translate and score individual sentences with Indic-Translate in real time.
2. Run unattended evaluations across datasets with live status reporting.
3. Inspect quality reports, MQM scores, and highlighted error spans.
4. Manage the human review queue, export review CSV, and re-calibrate thresholds.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import calibrate, config, report
from .bodhan import BodhanClient, CostTracker
from .cache import Cache
from .checks import ItemContext, build_registry
from .checks.base import dedupe_flags
from .dataset import load_dataset
from .report import _highlight_spans, mqm_score

log = logging.getLogger("bodhan_eval.server")

SUPPORTED_LANGS = [
    {"code": "hi", "name": "Hindi (हिंदी)"},
    {"code": "ta", "name": "Tamil (தமிழ்)"},
    {"code": "bn", "name": "Bengali (বাংলা)"},
    {"code": "te", "name": "Telugu (తెలుగు)"},
    {"code": "mr", "name": "Marathi (मराठी)"},
    {"code": "gu", "name": "Gujarati (ગુજરાતી)"},
    {"code": "kn", "name": "Kannada (ಕನ್ನಡ)"},
    {"code": "ml", "name": "Malayalam (മലയാളം)"},
    {"code": "pa", "name": "Punjabi (ਪੰਜਾਬੀ)"},
    {"code": "or", "name": "Odia (ଓଡ଼ିଆ)"},
    {"code": "as", "name": "Assamese (অসমীয়া)"},
    {"code": "ur", "name": "Urdu (اردو)"},
    {"code": "en", "name": "English"},
]


class HarnessServerHandler(BaseHTTPRequestHandler):
    report_dir: Path = Path("report")
    active_thresholds = config.Thresholds()
    human_labels: list[dict] = []

    def log_message(self, format, *args):
        # Clean server logging
        log.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _send_json(self, data: dict | list, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, content: str, status: int = 200):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            return self._handle_index()
        elif path in ("/site", "/site/", "/site/index.html"):
            site_file = Path("docs/site/index.html")
            if site_file.exists():
                content = site_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(content)
                return
        elif path in ("/sample-report", "/sample-report/", "/sample-report/index.html"):
            rep_file = Path("docs/sample_report/index.html")
            if rep_file.exists():
                content = rep_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(content)
                return
        elif path == "/api/status":
            return self._handle_status()
        elif path == "/api/health":
            return self._handle_health()
        elif path == "/api/report":
            return self._handle_report()
        elif path == "/api/items":
            return self._handle_items(parsed.query)
        elif path == "/api/diff":
            return self._handle_diff()
        elif path == "/api/review-queue":
            return self._handle_review_queue()
        elif path == "/api/export-review-queue":
            return self._handle_export_csv()
        elif path == "/api/datasets":
            return self._handle_datasets()
        elif path == "/api/health":
            return self._handle_health()

        # Static files in report dir
        local_file = self.report_dir / path.lstrip("/")
        if local_file.exists() and local_file.is_file():
            content = local_file.read_bytes()
            mime = "text/plain"
            if path.endswith(".html"): mime = "text/html"
            elif path.endswith(".json"): mime = "application/json"
            elif path.endswith(".wav"): mime = "audio/wav"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            body = json.loads(raw_body)
        except Exception:
            return self._send_json({"error": "Invalid JSON"}, 400)

        if path == "/api/check-single":
            return self._handle_check_single(body)
        elif path == "/api/run-harness":
            return self._handle_run_harness(body)
        elif path == "/api/review-label":
            return self._handle_review_label(body)
        elif path == "/api/calibrate":
            return self._handle_calibrate(body)

        self.send_error(404, "Not Found")

    # ---- Request Handlers ---------------------------------------------------

    def _handle_index(self):
        self._send_html(get_dashboard_html(self.report_dir))

    def _handle_status(self):
        config.load_env()
        summary = {}
        sum_file = self.report_dir / "summary.json"
        if sum_file.exists():
            try:
                summary = json.loads(sum_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        data = {
            "have_translate": config.have_translate(),
            "have_speak": config.have_speak(),
            "have_transcribe": config.have_transcribe(),
            "have_judge": config.have_judge(),
            "models": {
                "translate": config.TRANSLATE_MODEL,
                "speak": config.SPEAK_MODEL,
                "transcribe": config.TRANSCRIBE_MODEL,
                "judge": config.LLM_MODEL,
            },
            "languages": SUPPORTED_LANGS,
            "thresholds": {
                "length_lo": self.active_thresholds.length_lo,
                "length_hi": self.active_thresholds.length_hi,
                "script_min": self.active_thresholds.script_min,
            },
            "last_summary": summary,
        }
        self._send_json(data)

    def _handle_health(self):
        """Liveness + readiness probe for monitoring and load balancers.

        Returns 200 whenever the server can answer. It never makes a network
        call, so it is cheap and safe to poll frequently and cannot burn API
        credits; provider credentials are reported as configured/not configured
        only.
        """
        try:
            config.load_env()
            providers = {
                "translate": config.have_translate(),
                "speak": config.have_speak(),
                "transcribe": config.have_transcribe(),
                "judge": config.have_judge(),
            }
            ready = providers["translate"]
            self._send_json({
                "status": "ok" if ready else "degraded",
                "ready": ready,
                "service": "bodhan-eval",
                "version": "0.1.0",
                "report_dir": str(self.report_dir),
                "report_dir_available": self.report_dir.exists(),
                "providers_configured": providers,
                "judge_configured": providers["judge"],
                "judge_model": config.LLM_MODEL or None,
                "judge_base_url": config.LLM_BASE_URL or None,
                "models": {
                    "translate": config.TRANSLATE_MODEL,
                    "speak": config.SPEAK_MODEL,
                    "transcribe": config.TRANSCRIBE_MODEL,
                    "judge": config.LLM_MODEL,
                },
            }, 200)
        except Exception as exc:  # noqa: BLE001 - health probe must never crash the server
            log.exception("health check failed")
            self._send_json({"status": "error", "ready": False,
                             "error": str(exc)}, 500)

    def _handle_report(self):
        sum_file = self.report_dir / "summary.json"
        if not sum_file.exists():
            return self._send_json({"error": "No report summary available yet"}, 404)
        data = json.loads(sum_file.read_text(encoding="utf-8"))
        self._send_json(data)

    def _handle_items(self, query_str: str):
        items_file = self.report_dir / "items.jsonl"
        if not items_file.exists():
            return self._send_json([], 200)
        qs = parse_qs(query_str)
        lang_filter = qs.get("lang", [None])[0]
        cat_filter = qs.get("cat", [None])[0]
        status_filter = qs.get("status", [None])[0]
        q = qs.get("q", [""])[0].lower().strip()

        items = []
        for line in items_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            it = json.loads(line)
            if lang_filter and lang_filter != "all" and it.get("lang") != lang_filter:
                continue
            if status_filter == "flagged" and it.get("flag_count", 0) == 0:
                continue
            if status_filter == "clean" and it.get("flag_count", 0) > 0:
                continue
            if cat_filter and cat_filter != "all":
                if not any(f.get("category") == cat_filter for f in it.get("flags", [])):
                    continue
            if q:
                match = (q in it.get("id", "").lower() or
                         q in (it.get("source") or "").lower() or
                         q in (it.get("translation") or "").lower() or
                         any(q in (f.get("evidence") or "").lower() for f in it.get("flags", [])))
                if not match:
                    continue
            items.append(it)
        self._send_json(items)

    def _handle_diff(self):
        diff_file = self.report_dir / "diff.md"
        if not diff_file.exists():
            return self._send_json({"diff": "No baseline diff available. Run with --baseline to generate a diff."}, 200)
        self._send_json({"diff": diff_file.read_text(encoding="utf-8")}, 200)

    def _handle_datasets(self):
        data_dir = Path("data")
        files = []
        if data_dir.exists():
            for p in data_dir.glob("*.jsonl"):
                files.append({"name": p.name, "path": str(p), "size_kb": round(p.stat().st_size / 1024, 1)})
        self._send_json(files)

    def _handle_review_queue(self):
        items_file = self.report_dir / "items.jsonl"
        if not items_file.exists():
            return self._send_json([], 200)
        review = []
        for line in items_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            it = json.loads(line)
            # Items with flags or marked for human verification
            if it.get("flag_count", 0) > 0 or it.get("labels"):
                review.append(it)
        self._send_json(review)

    def _handle_export_csv(self):
        items_file = self.report_dir / "items.jsonl"
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["item_id", "lang", "flag_count", "mqm", "categories", "severities", "spans", "evidence", "human_label_verified"])
        if items_file.exists():
            for line in items_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                it = json.loads(line)
                if it.get("flag_count", 0) > 0:
                    cats = ";".join(f.get("category", "") for f in it.get("flags", []))
                    sevs = ";".join(f.get("severity", "") for f in it.get("flags", []))
                    spans = ";".join(str(f.get("span")) for f in it.get("flags", []))
                    evs = " | ".join(f.get("evidence", "") for f in it.get("flags", []))
                    writer.writerow([it.get("id"), it.get("lang"), it.get("flag_count"),
                                     it.get("mqm"), cats, sevs, spans, evs, ""])
        csv_bytes = buf.getvalue().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="review_queue.csv"')
        self.send_header("Content-Length", str(len(csv_bytes)))
        self.end_headers()
        self.wfile.write(csv_bytes)

    def _handle_check_single(self, body: dict):
        source = str(body.get("source", "")).strip()
        lang = str(body.get("lang", "hi")).strip()
        translation = str(body.get("translation", "")).strip()
        auto_translate = bool(body.get("auto_translate", False))
        ref = body.get("reference")

        if not source:
            return self._send_json({"error": "source text cannot be empty"}, 400)

        config.load_env()

        # If live translation requested via Bodhan Indic-Translate
        if auto_translate or not translation:
            if not config.have_translate():
                return self._send_json({
                    "error": "BODHAN_TRANSLATE_KEY not configured. Set it in .env or provide a translation text."
                }, 400)
            try:
                cache = Cache(Path(".cache/bodhan_eval"))
                costs = CostTracker()
                async def _tr():
                    async with BodhanClient(cache, costs) as client:
                        return await client.translate(source, lang)
                translation = asyncio.run(_tr())
            except Exception as e:
                return self._send_json({"error": f"Indic-Translate API error: {e}"}, 500)

        # Build item and run deterministic check suite
        ctx = ItemContext(
            id="live-tester",
            source=source,
            lang=lang,
            translation=translation,
            reference=ref,
            labels=[],
        )

        reg = build_registry(self.active_thresholds)
        raw_flags = []
        checks_run = []
        for name, _tier, fn in reg:
            try:
                raw_flags.extend(fn(ctx))
                checks_run.append(name)
            except Exception as e:
                log.warning("check %s failed on single-item: %s", name, e)

        flags = dedupe_flags(raw_flags)
        words = len(translation.split())
        score = mqm_score(flags, words)
        highlighted = _highlight_spans(translation, flags)

        res = {
            "ok": True,
            "source": source,
            "translation": translation,
            "lang": lang,
            "mqm": score,
            "flags": [f.to_dict() for f in flags],
            "flag_count": len(flags),
            "highlighted": highlighted,
            "checks_run": checks_run,
        }
        self._send_json(res)

    def _handle_run_harness(self, body: dict):
        dataset_path = Path(body.get("dataset", "data/demo.jsonl"))
        langs = body.get("langs", ["hi", "ta", "bn"])
        generate = bool(body.get("generate", False))
        offline = bool(body.get("offline", True))
        inject_errors = bool(body.get("inject", False))
        baseline = Path(body["baseline"]) if body.get("baseline") else None
        max_cost = float(body["max_cost_inr"]) if body.get("max_cost_inr") else None

        if not dataset_path.exists():
            return self._send_json({"error": f"Dataset {dataset_path} not found"}, 404)

        from .cli import build_parser, _run
        parser = build_parser()
        cmd_args = ["--dataset", str(dataset_path), "--langs", ",".join(langs), "--out", str(self.report_dir)]
        if generate: cmd_args.append("--generate")
        if offline: cmd_args.append("--offline")
        if inject_errors: cmd_args.append("--inject")
        if baseline: cmd_args.extend(["--baseline", str(baseline)])
        if max_cost: cmd_args.extend(["--max-cost-inr", str(max_cost)])

        parsed_args = parser.parse_args(cmd_args)
        cfg = config.RunConfig(
            dataset=dataset_path, langs=langs, out=self.report_dir,
            generate=generate, baseline=baseline, max_cost_inr=max_cost,
            offline=offline, thresholds=self.active_thresholds,
        )

        try:
            exit_code = asyncio.run(_run(cfg, parsed_args))
            sum_file = self.report_dir / "summary.json"
            summary = json.loads(sum_file.read_text(encoding="utf-8")) if sum_file.exists() else {}
            self._send_json({"ok": exit_code == 0, "exit_code": exit_code, "summary": summary})
        except Exception as e:
            log.exception("Harness run error")
            self._send_json({"error": str(e)}, 500)

    def _handle_review_label(self, body: dict):
        item_id = body.get("id")
        verified = body.get("verified", True)
        self.human_labels.append({"id": item_id, "verified": verified, "timestamp": str(asyncio.get_event_loop().time()) if asyncio.get_event_loop().is_running() else ""})
        self._send_json({"ok": True, "count": len(self.human_labels)})

    def _handle_calibrate(self, body: dict):
        cal_path = Path("data/calibration.jsonl")
        records = []
        if cal_path.exists():
            for line in cal_path.read_text(encoding="utf-8").splitlines():
                if not line.strip(): continue
                it = json.loads(line)
                labels = it.get("labels") or []
                ratio = len((it.get("translation") or "").split()) / max(1, len((it.get("source") or "").split()))
                lbl = None
                for l in labels:
                    if l.get("type") in ("omission", "addition"):
                        lbl = l.get("type")
                        break
                records.append({"ratio": ratio, "label": lbl})

        sweep = calibrate.sweep_length(records)
        chosen = sweep.get("chosen", {})
        self.active_thresholds.length_lo = chosen.get("lo", 0.55)
        self.active_thresholds.length_hi = chosen.get("hi", 2.20)
        self._send_json({
            "ok": True,
            "chosen": chosen,
            "records_used": len(records),
            "updated_thresholds": {
                "length_lo": self.active_thresholds.length_lo,
                "length_hi": self.active_thresholds.length_hi,
            }
        })


_TEMPLATE_DIR = Path(__file__).with_name("templates")


def _load_dashboard_template() -> str:
    """Read the dashboard HTML template, falling back to a minimal error page.

    The dashboard is a large static asset with two substitution slots
    (``__ITEMS_JSON__`` and ``__SUMMARY_JSON__``). Keeping it in its own file
    keeps this module focused on HTTP handling and lets the page be edited
    without touching Python.

    The template is validated on every request because a partially written or
    truncated file serves as a head-only document, which browsers render as a
    blank white page instead of surfacing an error.
    """
    template_path = _TEMPLATE_DIR / "dashboard.html"
    try:
        template = template_path.read_text(encoding="utf-8")
        missing = [tok for tok in ("__ITEMS_JSON__", "__SUMMARY_JSON__")
                   if tok not in template]
        if missing:
            raise OSError(
                f"template is incomplete (missing {', '.join(missing)}); "
                "the file may have been truncated"
            )
        if "<body" not in template:
            raise OSError(
                "template has no <body> element; the file may have been truncated"
            )
        return template
    except OSError as exc:  # missing/unreadable/truncated must not 500 the UI
        log.error("dashboard template unusable at %s: %s", template_path, exc)
        from html import escape as _escape
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Dashboard unavailable</title></head><body>"
            "<h1>Dashboard unavailable</h1>"
            f"<p>Could not load dashboard template: {_escape(str(exc))}</p>"
            "</body></html>"
        )


def get_dashboard_html(report_dir: Path) -> str:
    """Render the interactive single-page app from report data + template."""
    summary_path = report_dir / "summary.json"
    summary_json = ("{}"
                    if not summary_path.exists()
                    else summary_path.read_text(encoding="utf-8"))

    items_path = report_dir / "items.jsonl"
    items_list: list[dict] = []
    if items_path.exists():
        for line in items_path.read_text(encoding="utf-8").splitlines()[:200]:
            if not line.strip():
                continue
            try:
                items_list.append(json.loads(line))
            except json.JSONDecodeError as exc:
                log.warning("skipping malformed dashboard item: %s", exc.msg)
    items_json = json.dumps(items_list, ensure_ascii=False)

    template = _load_dashboard_template()
    # Token substitution avoids clashing with the template's literal CSS/JS braces.
    return (template
            .replace("__ITEMS_JSON__", items_json)
            .replace("__SUMMARY_JSON__", summary_json))



class _DualStackHTTPServer(ThreadingHTTPServer):
    """HTTP server that accepts connections over both IPv6 and IPv4."""

    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            # One socket for both families so localhost and 127.0.0.1 agree.
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError:
            pass
        super().server_bind()


def _make_server(port: int) -> ThreadingHTTPServer:
    """Bind a ThreadingHTTPServer that serves both IPv6 and IPv4 clients.

    ``localhost`` resolves to ``::1`` on modern systems. Binding only
    ``0.0.0.0`` makes those requests fail to connect, so the server prefers a
    dual-stack ``::`` socket and falls back to IPv4 only where IPv6 is absent.
    """
    try:
        return _DualStackHTTPServer(("::", port), HarnessServerHandler)
    except OSError as exc:
        log.warning("dual-stack bind unavailable (%s); using IPv4 only", exc)
        return ThreadingHTTPServer(("0.0.0.0", port), HarnessServerHandler)


def run_server(port: int = 8080, report_dir: Path | str = "report") -> int:
    """Run the interactive quality control web server."""
    HarnessServerHandler.report_dir = Path(report_dir)
    httpd = _make_server(port)
    bound_port = httpd.server_address[1]
    log.info("Bodhan Quality Control Center running at http://localhost:%d", bound_port)
    print(f"Bodhan Quality Control Center running at http://localhost:{bound_port}")
    print(f"Also reachable at http://127.0.0.1:{bound_port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--report", type=Path, default=Path("report"))
    args = p.parse_args()
    run_server(port=args.port, report_dir=args.report)
