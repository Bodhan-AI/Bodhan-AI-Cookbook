"""Drive the MCP server with an independent client (MCP Inspector CLI) and save a transcript.

    python scripts/client_check.py [--env-file .env] [--paid] [--out evaluation/clients/inspector.md]

Runs, as separate processes, the pinned MCP Inspector CLI against:
  * stdio: tools/list, resources/list, resources/read, a free tool call, bad inputs;
  * Streamable HTTP with a bearer token: requests without / with a wrong token are refused,
    and the same client with the token lists tools and calls one.
With --paid it also makes one small real Bodhan call per transport (a one-sentence
translation, well under ₹0.01). Bodhan keys are read by the server from --env-file; they
never appear on a command line or in the transcript. Needs Node.js (npx).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
INSPECTOR = "@modelcontextprotocol/inspector@2.13.0"
SENTENCE = "The school opens at nine in the morning."


sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_runs import ABS_PATH, SECRET  # noqa: E402  (same scrubbing rules as the run export)


def scrub(text: str, *extra: Path) -> str:
    for base, repl in [(ROOT, "<recipe>"), *[(p, "<tmp>") for p in extra]]:
        b = str(base)
        for v in (b.replace("\\", "\\" * 4), b.replace("\\", "\\" * 2), b, b.replace("\\", "/")):  # JSON-in-JSON, JSON, plain, slashes
            text = text.replace(v, repl)
    return SECRET.sub("<secret>", ABS_PATH.sub("<local-path>", text))


def short(text: str, limit: int = 1400) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f"\n… [{len(text) - limit} more chars]"


class Transcript:
    def __init__(self, tmp: Path):
        self.lines: list[str] = []
        self.tmp = tmp
        self.failures: list[str] = []

    def record(self, title: str, shown_cmd: str, output: str, ok: bool) -> None:
        if not ok:
            self.failures.append(title)
        self.lines += [f"### {'PASS' if ok else 'FAIL'}: {title}", "", "```text", f"$ {scrub(shown_cmd, self.tmp)}", short(scrub(output, self.tmp)), "```", ""]


def inspector(target: list[str], method_args: list[str], timeout: float = 180) -> tuple[int, str]:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise SystemExit("npx (Node.js) is required for this check")
    r = subprocess.run([npx, "-y", INSPECTOR, "--cli", *target, *method_args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, cwd=ROOT)
    return r.returncode, (r.stdout + ("\n[stderr]\n" + r.stderr if r.stderr.strip() else ""))


def parsed(out: str):
    try:
        return json.loads(out.split("\n[stderr]")[0])
    except ValueError:
        return None


def tool_result(out: str) -> dict:
    """Inspector prints the CallToolResult; our tools return JSON text content."""
    data = parsed(out) or {}
    sc = data.get("structuredContent")
    if isinstance(sc, dict):
        return sc
    for c in data.get("content", []):
        try:
            return json.loads(c.get("text", ""))
        except ValueError:
            continue
    return {}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=None, help="file the server reads the Bodhan keys from (only needed with --paid)")
    ap.add_argument("--paid", action="store_true", help="also make one small real Bodhan call per transport")
    ap.add_argument("--out", default=str(ROOT / "evaluation" / "clients" / "inspector.md"))
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="client_check_"))
    t = Transcript(tmp)
    env_args = ["--env-file", str(Path(a.env_file).resolve())] if a.env_file else []
    server = [sys.executable, "-m", "bodhan_mcp", "--artifacts-dir", str(tmp / "art"), "--no-cache", *env_args]
    shown = "npx " + INSPECTOR + " --cli python -m bodhan_mcp --artifacts-dir <tmp>/art --no-cache" + (" --env-file <env-file>" if env_args else "")

    # ---- stdio ------------------------------------------------------------------
    code, out = inspector(server, ["--method", "tools/list"])
    names = [x["name"] for x in (parsed(out) or {}).get("tools", [])]
    t.record("stdio tools/list", shown + " --method tools/list", f"exit {code}; {len(names)} tools: {', '.join(names)}", code == 0 and len(names) >= 12)
    code, out = inspector(server, ["--method", "resources/list"])
    uris = [x["uri"] for x in (parsed(out) or {}).get("resources", [])]
    t.record("stdio resources/list", shown + " --method resources/list", f"exit {code}; {uris}", code == 0 and "bodhan://voices" in uris)
    code, out = inspector(server, ["--method", "resources/read", "--uri", "bodhan://voices"])
    t.record("stdio resources/read bodhan://voices", shown + " --method resources/read --uri bodhan://voices", out, code == 0 and "Kavya" in out)
    code, out = inspector(server, ["--method", "resources/read", "--uri", "bodhan://errors"])
    t.record("stdio resources/read bodhan://errors", shown + " --method resources/read --uri bodhan://errors", out,
             code == 0 and "unsupported_language" in out and "retryable" in out)
    code, out = inspector(server, ["--method", "prompts/list"])
    names_p = [x["name"] for x in (parsed(out) or {}).get("prompts", [])]
    t.record("stdio prompts/list", shown + " --method prompts/list", f"exit {code}; {names_p}", code == 0 and "plan_media_task" in names_p)
    code, out = inspector(server, ["--method", "prompts/get", "--prompt-name", "plan_media_task", "--prompt-args",
                                   "task=Make a Tamil audio summary of this chapter", "files=chapter.pdf"])
    t.record("stdio prompts/get plan_media_task", shown + " --method prompts/get --prompt-name plan_media_task --prompt-args task=... files=...",
             out, code == 0 and "Tamil audio summary" in out)
    code, out = inspector(server, ["--method", "tools/call", "--tool-name", "detect_language", "--tool-arg", "text=நீர் சுழற்சி பாடம்"])
    r = tool_result(out)
    t.record("stdio free call detect_language", shown + " --method tools/call --tool-name detect_language --tool-arg text=...", out,
             code == 0 and (r.get("detection") or {}).get("language") == "ta")
    bad = [("unsupported target language", ["--tool-name", "translate_text", "--tool-arg", "text=Hello", "--tool-arg", "target_language=fr"], "unsupported_language"),
           ("missing file", ["--tool-name", "ocr_document", "--tool-arg", f"path={tmp / 'missing.png'}"], "file_not_found"),
           ("unknown voice", ["--tool-name", "synthesize_speech", "--tool-arg", "text=নমস্কার", "--tool-arg", "language=bn", "--tool-arg", "voice=Nobody"],
            "unsupported_voice"),
           # Inspector checks the name against tools/list and refuses before sending; the server's own unknown_tool
           # error is exercised through the MCP Python SDK client in tests/test_mcp_stdio.py.
           ("unknown tool (refused by the client before sending)", ["--tool-name", "no_such_tool"], "tool_not_found")]
    for title, args_, want in bad:
        code, out = inspector(server, ["--method", "tools/call", *args_])
        got = (tool_result(out).get("error") or {}).get("code")
        t.record(f"stdio bad input: {title} -> {want}", shown + " --method tools/call " + " ".join(args_), out, got == want or f'"{want}"' in out)
    if a.paid:
        code, out = inspector(server, ["--method", "tools/call", "--tool-name", "translate_text", "--tool-arg", f"text={SENTENCE}", "--tool-arg",
                                       "target_language=hi"])
        r = tool_result(out)
        t.record("stdio real Bodhan call translate_text en->hi", shown + f' --method tools/call --tool-name translate_text --tool-arg "text={SENTENCE}" '
                 "--tool-arg target_language=hi", out, code == 0 and r.get("ok") is True)

    # ---- Streamable HTTP with a token ---------------------------------------------
    port = free_port()
    token_file = tmp / "token"
    proc = subprocess.Popen([*server, "--transport", "http", "--port", str(port), "--token-file", str(token_file)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=ROOT)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(100):
            if token_file.exists() and token_file.read_text().strip():
                try:
                    httpx.get(url, timeout=1)
                    break
                except httpx.HTTPError:
                    pass
            time.sleep(0.2)
        token = token_file.read_text().strip()
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "client_check", "version": "1"}}}
        hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        for title, extra in (("no token", {}), ("wrong token", {"Authorization": "Bearer wrong"})):
            resp = httpx.post(url, json=init, headers={**hdr, **extra}, timeout=10)
            t.record(f"HTTP {title} is refused", f"POST {url.replace(str(port), '<port>')} initialize ({title})", f"HTTP {resp.status_code} {resp.text[:200]}",
                     resp.status_code == 401)
        auth = ["--transport", "http", "--header", f"Authorization: Bearer {token}"]
        code, out = inspector([url], [*auth, "--method", "tools/list"])
        names = [x["name"] for x in (parsed(out) or {}).get("tools", [])]
        shown_http = f"npx {INSPECTOR} --cli http://127.0.0.1:<port>/mcp --transport http --header 'Authorization: Bearer <token>'"
        t.record("HTTP with token tools/list", shown_http + " --method tools/list", f"exit {code}; {len(names)} tools", code == 0 and len(names) >= 12)
        code, out = inspector([url], [*auth, "--method", "tools/call", "--tool-name", "list_voices", "--tool-arg", "language=ta"])
        t.record("HTTP with token free call list_voices", shown_http + " --method tools/call --tool-name list_voices --tool-arg language=ta", out,
                 code == 0 and tool_result(out).get("ok") is True)
        if a.paid:
            code, out = inspector([url], [*auth, "--method", "tools/call", "--tool-name", "translate_text", "--tool-arg", f"text={SENTENCE}",
                                          "--tool-arg", "target_language=ta"])
            t.record("HTTP with token real Bodhan call translate_text en->ta", shown_http + f' --method tools/call --tool-name translate_text '
                     f'--tool-arg "text={SENTENCE}" --tool-arg target_language=ta', out, code == 0 and tool_result(out).get("ok") is True)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    head = [f"# Independent MCP client check ({INSPECTOR})", "",
            f"Generated by `python scripts/client_check.py{' --paid' if a.paid else ''}` on {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}, "
            f"{sys.platform}, Python {sys.version.split()[0]}, Node {subprocess.run(['node', '--version'], capture_output=True, text=True).stdout.strip()}. "
            f"Server source: commit `{subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, cwd=ROOT).stdout.strip()}`.", "",
            f"Result: {len(t.failures)} failed check(s)." + (f" Failed: {', '.join(t.failures)}" if t.failures else " All checks passed."), ""]
    text = "\n".join(head + t.lines)
    for secret_like in (os.environ.get(k) for k in ("BODHAN_TRANSLATE_KEY", "BODHAN_OCR_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY")):
        if secret_like and secret_like in text:
            raise SystemExit("refusing to write: a key appeared in the transcript")
    out_path.write_text(text, encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{len(t.failures)} failed; transcript: {out_path}")
    sys.exit(1 if t.failures else 0)


if __name__ == "__main__":
    main()
