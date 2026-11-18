"""The optional trace viewer: read-only, stdlib only, never touches the run folder."""
from __future__ import annotations

import urllib.error
import urllib.request

from bodhan_agent.webui import serve


def test_viewer_serves_the_page_the_trace_and_nothing_else(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "trace.jsonl").write_text('{"type": "plan", "steps": []}\n', encoding="utf-8")
    (run / "report.json").write_text('{"status": "completed"}', encoding="utf-8")
    httpd = serve(str(run), port=0, block=False)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "Bodhan task run" in page and "trace.jsonl" in page
        assert urllib.request.urlopen(base + "/trace.jsonl").read().decode().startswith('{"type": "plan"')
        assert urllib.request.urlopen(base + "/report.json").read().decode() == '{"status": "completed"}'
        try:
            urllib.request.urlopen(base + "/../etc/passwd")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("path traversal should 404")
    finally:
        httpd.shutdown()
        httpd.server_close()
