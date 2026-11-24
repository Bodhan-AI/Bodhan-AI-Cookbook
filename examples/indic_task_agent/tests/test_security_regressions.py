"""Findings of the 20 November 2026 security review (M1, L1, L2, L3), each reproduced against the
real entry point: the server as its own process, the viewer over HTTP, resolve_input directly."""

import os
import pathlib
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest
from fakes import FakeBodhan
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from bodhan_agent.viewer import serve
from bodhan_mcp import errors as E
from bodhan_mcp.server import default_read_roots, default_token_path


def stdio_server(cwd, *extra):
    env = {"PYTHONIOENCODING": "utf-8", **{k: os.environ[k] for k in ("PYTHONPATH", "SYSTEMROOT") if k in os.environ}}
    return StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp", "--artifacts-dir", str(cwd / "art"), "--no-cache", *extra],
                                 env=env, cwd=str(cwd))


async def read(c, path):
    r = await c.call_tool("read_text", {"path": path})
    return r.structured_content["error"]["code"] if r.is_error else "ok"


async def test_m1_the_standalone_server_reads_only_below_its_folder_by_default(tmp_path):
    srv = tmp_path / "srv"
    srv.mkdir()
    (tmp_path / "fake.env").write_text("BODHAN_TRANSLATE_KEY=secret", encoding="utf-8")
    (srv / ".env").write_text("BODHAN_TRANSLATE_KEY=secret", encoding="utf-8")
    (srv / "note.txt").write_text("A note.", encoding="utf-8")
    async with Client(stdio_server(srv)) as c:  # the README's mcpServers block: no --allow-dir
        assert await read(c, str(tmp_path / "fake.env")) == "path_not_allowed"
        assert await read(c, "../fake.env") == "path_not_allowed"
        assert await read(c, ".env") == "path_not_allowed"
        assert await read(c, str(srv / ".env")) == "path_not_allowed"
        assert await read(c, "note.txt") == "ok"
    async with Client(stdio_server(srv, "--allow-any-path")) as c:  # the explicit opt-out
        assert await read(c, str(tmp_path / "fake.env")) == "ok"
        assert await read(c, ".env") == "path_not_allowed"  # hidden files stay unreadable even then


def test_m1_no_default_read_root_when_started_in_home_or_the_filesystem_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert default_read_roots() == []
    (tmp_path / "project").mkdir()
    monkeypatch.chdir(tmp_path / "project")
    assert default_read_roots() == [(tmp_path / "project").resolve()]
    monkeypatch.chdir(pathlib.Path(tmp_path.anchor))
    assert default_read_roots() == []


@pytest.mark.parametrize("raw", [r"\\attacker.example\share\x.txt", "//attacker.example/share/x.txt", "file://attacker.example/x.txt",
                                 "https://attacker.example/x.txt"])
def test_l2_network_paths_are_refused_before_any_filesystem_call(make_ctx, monkeypatch, raw):
    ctx = make_ctx(FakeBodhan())
    touched = []
    for name in ("resolve", "exists", "is_file"):
        monkeypatch.setattr(pathlib.Path, name, lambda self, *a, _n=name, **k: touched.append(_n))
    with pytest.raises(E.ToolError) as exc:
        ctx.resolve_input(raw)
    assert exc.value.code == E.PATH_NOT_ALLOWED and touched == []


def test_l3_the_default_http_token_lives_outside_the_artifacts_folder(tmp_path):
    with socket.create_server(("127.0.0.1", 0)) as s:
        port = s.getsockname()[1]
    env = {"PATH": "", "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "APPDATA": str(tmp_path / "cfg"), "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
           "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    proc = subprocess.Popen([sys.executable, "-m", "bodhan_mcp", "--transport", "http", "--port", str(port), "--artifacts-dir",
                             str(tmp_path / "art"), "--no-cache"], stderr=subprocess.PIPE, cwd=tmp_path, env=env)
    token = tmp_path / "cfg" / "bodhan_mcp" / "http_token"
    try:
        for _ in range(100):
            if token.is_file() and token.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.1)
        assert len(token.read_text(encoding="utf-8").strip()) >= 32
        assert [p.name for p in (tmp_path / "art").iterdir() if "token" in p.name] == []
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_l3_default_token_path_follows_the_platform_config_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_token_path() == tmp_path / "bodhan_mcp" / "http_token"


def test_l1_the_viewer_refuses_a_foreign_host_header(tmp_path):
    out = tmp_path / "out"
    (out / "artifacts").mkdir(parents=True)
    (out / "artifacts" / "summary.md").write_text("private translation", encoding="utf-8")
    httpd = serve(out, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_port
    base = f"http://127.0.0.1:{port}"
    try:
        for host in ("attacker.example", f"attacker.example:{port}", "127.0.0.1:1", ""):
            for path in ("/", "/api/state", "/artifacts/summary.md"):
                assert httpx.get(base + path, headers={"Host": host}).status_code == 403, (host, path)
        for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            r = httpx.get(base + "/artifacts/summary.md", headers={"Host": host})
            assert r.status_code == 200 and r.text == "private translation"
    finally:
        httpd.shutdown()


# --- mixed-separator and device paths, finish and viewer ---------------------------------------------------------

NETWORK_FORMS = [r"/\attacker.example\share\x.txt", r"\/attacker.example/share/x.txt", r"\\?\UNC\attacker.example\share\x.txt",
                 r"\\.\pipe\x", r"\\attacker.example\share\x.txt", "//attacker.example/share/x.txt", "FILE://attacker.example/x.txt"]


def network_resolves(monkeypatch):
    """Record every Path.resolve of a network form (a Windows SMB lookup), while letting local paths resolve."""
    seen, real = [], pathlib.Path.resolve

    def resolve(self, *a, **k):
        if str(self).replace("\\", "/").startswith("//"):
            seen.append(str(self))
            raise AssertionError(f"network path reached the filesystem: {self}")
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "resolve", resolve)
    return seen


@pytest.mark.parametrize("raw", NETWORK_FORMS)
def test_mixed_separator_and_device_forms_are_refused_before_any_filesystem_call(make_ctx, monkeypatch, raw):
    ctx = make_ctx(FakeBodhan())
    touched = []
    for name in ("resolve", "exists", "is_file"):
        monkeypatch.setattr(pathlib.Path, name, lambda self, *a, _n=name, **k: touched.append(_n))
    with pytest.raises(E.ToolError) as exc:
        ctx.resolve_input(raw)
    assert exc.value.code == E.PATH_NOT_ALLOWED and touched == []


async def test_finish_never_resolves_a_network_deliverable_path(tmp_path, monkeypatch):
    from fakes import ScriptedChat
    from test_agent_e2e import args, last_ok

    from bodhan_agent.cli import run
    from bodhan_agent.llm import PlannerLLM
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")
    src = tmp_path / "note.txt"
    src.write_text("A note.", encoding="utf-8")
    plan = [("record_plan", {"steps": ["write"], "deliverables": [{"kind": "text", "language": "en"}]})]
    write = [("write_text", {"name": "summary.md", "content": "A short English summary."})]
    bad = [("finish", {"status": "completed", "final_answer": "Done.", "deliverables": NETWORK_FORMS})]
    good = lambda m: [("finish", {"status": "completed", "final_answer": "Done.", "deliverables": [last_ok(m, "path")]})]  # noqa: E731
    seen = network_resolves(monkeypatch)
    report = await run(args(tmp_path, "Summarise this note in English", [src]), transport=FakeBodhan().transport,
                       llm=PlannerLLM(client=ScriptedChat([plan, write, bad, good]), model="scripted"))
    assert seen == []
    assert report["status"] == "completed" and report["deliverables"][0]["path"].endswith("summary.md")  # a valid local output still resolves


def test_viewer_artifact_urls_cannot_form_a_network_path(tmp_path, monkeypatch):
    out = tmp_path / "out"
    (out / "artifacts").mkdir(parents=True)
    (out / "artifacts" / "summary.md").write_text("private translation", encoding="utf-8")
    httpd = serve(out, 0)
    seen = network_resolves(monkeypatch)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base, host = f"http://127.0.0.1:{httpd.server_port}", {"Host": f"127.0.0.1:{httpd.server_port}"}
    try:
        # any web page can request these (the browser sends the right Host): each joined onto artifacts/ would be a UNC path
        for path in ("%5C%5Cattacker.example%5Cshare%5Cx.mp3", "//attacker.example/share/x.mp3", "%2F%5Cattacker.example%5Cs%5Cx",
                     "..%5C..%5Csecret.txt", "C:%5Cx.txt"):
            assert httpx.get(f"{base}/artifacts/{path}", headers=host).status_code == 404, path
        assert httpx.get(f"{base}/artifacts/summary.md", headers=host).text == "private translation"
        assert seen == []
    finally:
        httpd.shutdown()


def test_viewer_on_port_80_accepts_the_host_header_a_browser_sends(tmp_path):
    out = tmp_path / "out"
    (out / "artifacts").mkdir(parents=True)
    httpd = serve(out, 0)
    port = httpd.server_port
    httpd.server_address = ("127.0.0.1", 80)  # the handler reads the port from here; a real bind to 80 needs privileges
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        for host, code in (("localhost", 200), ("127.0.0.1", 200), ("localhost:80", 200), ("attacker.example", 403), ("localhost:8080", 403)):
            assert httpx.get(f"http://127.0.0.1:{port}/", headers={"Host": host}).status_code == code, host
    finally:
        httpd.shutdown()


def test_a_doubled_backslash_windows_path_is_local_not_a_url(tmp_path):
    # h12/h13 with gemini-3.5-flash-lite (e2c1302): the planner sent deliverable paths as "E:\\out\\a.mp3"; normalising
    # separators turned that into "E://out//a.mp3", which the URL check refused, so real artifacts were "not produced"
    from bodhan_mcp.runtime import is_network_path
    assert not is_network_path(r"E:\\out\\artifacts\\a.mp3") and not is_network_path("C://work/a.md")
    assert is_network_path("file://host/x") and is_network_path("https://example.org/x") and is_network_path(r"\\host\share")
