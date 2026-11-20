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
