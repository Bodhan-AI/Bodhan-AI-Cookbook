"""The live run viewer: state follows the trace as it grows, files are served only from
artifacts/, and untrusted text is never interpreted as HTML."""

import json
import threading

import httpx

from bodhan_agent.viewer import PAGE, serve


def test_viewer_follows_the_run_and_stays_inside_artifacts(tmp_path):
    out = tmp_path / "out"
    (out / "artifacts").mkdir(parents=True)
    (out / "artifacts" / "summary.md").write_text("<script>alert(1)</script> injected", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("private", encoding="utf-8")
    trace = out / "trace.jsonl"
    trace.write_text(json.dumps({"type": "run_start", "task": "Translate <b>this</b>"}) + "\n", encoding="utf-8")
    httpd = serve(out, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        s = httpx.get(base + "/api/state").json()
        assert s["task"] == "Translate <b>this</b>" and s["steps"] == [] and s["status"] is None
        with trace.open("a", encoding="utf-8") as f:  # the run moves on
            f.write(json.dumps({"type": "plan", "steps": ["ocr", "translate"], "deliverables": [{"kind": "translation", "language": "mr"}]}) + "\n")
            f.write(json.dumps({"type": "api_call", "cache": "miss", "cost_inr": 0.2}) + "\n")
            f.write(json.dumps({"type": "tool_call", "step": 2, "tool": "ocr_document", "ok": True, "rationale": "read the page",
                                "result_summary": {"verification": {"status": "pass"}}, "latency_s": 1.2, "cost_inr": 0.2}) + "\n")
            f.write('{"type": "partial line being writ')
        s = httpx.get(base + "/api/state").json()
        assert s["plan"]["steps"] == ["ocr", "translate"] and s["steps"][0]["tool"] == "ocr_document" and s["cost"]["bodhan_inr"] == 0.2
        r = httpx.get(base + "/artifacts/summary.md")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
        for bad in ("/artifacts/../../secret.txt", "/artifacts/%2e%2e/%2e%2e/secret.txt", "/artifacts/..%5c..%5csecret.txt", "/trace.jsonl"):
            assert httpx.get(base + bad).status_code == 404, bad
    finally:
        httpd.shutdown()
    assert "innerHTML" not in PAGE and "textContent" in PAGE
