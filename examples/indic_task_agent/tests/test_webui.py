"""The optional run viewer: read-only, stdlib only, confined to the chosen folder."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from bodhan_agent.webui import serve


def _get(url: str) -> bytes:
    return urllib.request.urlopen(url).read()


@pytest.fixture
def viewer(tmp_path):
    root = tmp_path / "runs"
    run = root / "task_a"
    (run / "artifacts").mkdir(parents=True)
    (run / "trace.jsonl").write_text('{"type": "plan", "steps": []}\n', encoding="utf-8")
    (run / "report.json").write_text(json.dumps({"status": "completed", "budget": {"cost_inr": 0.5}}), encoding="utf-8")
    (run / "result.md").write_text("# done", encoding="utf-8")
    (run / "artifacts" / "out.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    httpd = serve(str(root), port=0, block=False)
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_viewer_lists_runs_and_serves_their_files(viewer):
    assert b"Task runs" in _get(viewer + "/")
    assert json.loads(_get(viewer + "/api/runs")) == [{"name": "task_a", "status": "completed", "cost": 0.5}]
    assert _get(viewer + "/run/task_a/trace.jsonl").startswith(b'{"type": "plan"')
    assert _get(viewer + "/run/task_a/result.md") == b"# done"
    assert _get(viewer + "/run/task_a/artifacts/out.txt") == b"hello"


@pytest.mark.parametrize(
    "path",
    ["/run/task_a/../../secret.txt", "/run/task_a/artifacts/../../../secret.txt", "/run/other/trace.jsonl", "/run/task_a/.env", "/etc/passwd"],
)
def test_viewer_serves_nothing_outside_a_run(viewer, path):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(viewer + path)
    assert exc.value.code == 404
