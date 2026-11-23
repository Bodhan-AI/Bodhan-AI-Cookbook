"""JARVIS web UI server — zero extra dependencies (Python standard library only).

    python server.py                 # then open http://127.0.0.1:8765
    PORT=9000 python server.py       # different port
    python server.py --no-browser    # don't auto-open a browser tab

llm.py is used exactly as-is:
    POST /api/chart   {"query": "..."}          -> llm.make_charts(query)
    POST /api/render  {"tool": "...", "args": {}} -> llm.build_figure(tool, args)  (gallery demos, no LLM call)
    GET  /api/meta                               -> model name + tool list + active knowledge graph
    GET  /            -> web/voyage.html (Mahabharata voyage)      GET /classic -> web/index.html (original UI)

The browser receives each tool call's arguments and renders them with animated ECharts.
The Plotly figure built by charts/ is also sent along, as a fallback renderer.
"""
import base64
import binascii
import json
import mimetypes
import os
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT))

import llm  # noqa: E402
import Bodhan  # noqa: E402  (Indic translate / transcribe / speak)
from llm import MODEL, TOOLS, build_figure, make_charts  # noqa: E402

# Active knowledge graph (chosen in llm.py via KNOWLEDGE_GRAPH=mahabharata|helios), reported to the browser.
GRAPH_INFO = {"name": llm.GRAPH_NAME,
              "root": {"mahabharata": "Mahabharata", "helios": "Helios Dynamics Group"}[llm.GRAPH_NAME]}
print(f"[graph] knowledge graph: {llm.GRAPH_NAME}", file=sys.stderr)


def graph_outline(node):
    """Key names only (no fact text): {key: {...}} for branches, a fact count for leaves."""
    if isinstance(node, dict):
        return {str(k): graph_outline(v) for k, v in node.items()}
    return len(node) if isinstance(node, (list, str)) else 0


GRAPH_OUTLINE = {**GRAPH_INFO, "tree": graph_outline(llm.GRAPH)}  # served at /api/graph (the browser draws it)

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")

MAX_BODY = 2_000_000  # bytes


def clean(o):
    """Make any structure strict-JSON safe: NaN/Infinity -> None (browsers' JSON.parse rejects them)."""
    if isinstance(o, float):
        return o if o == o and o not in (float("inf"), float("-inf")) else None
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def figure_json(fig):
    """Plotly figure -> plain JSON dict (used by the browser only if the ECharts renderer fails)."""
    if fig is None:
        return None
    try:
        return clean(json.loads(fig.to_json()))
    except Exception:  # never let the fallback break the main response
        return None


def serialize(results):
    return [{"tool": r.get("tool"), "args": clean(r.get("args") or {}), "error": r.get("error"),
             "text": r.get("text"), "figure": figure_json(r.get("figure"))} for r in results]


def retrieval_json(results):
    """Graph traversal + retrieved context (attached by llm.make_charts), trimmed for the browser."""
    r = next((x.get("retrieval") for x in results if x.get("retrieval")), None)
    if not r:
        return None
    return clean({
        "elapsed_ms": r.get("elapsed_ms"), "error": r.get("error"), "trace": r.get("trace", []),
        "context": [{"path": c["path"], "chars": len(c["content"]), "content": c["content"][:700]}
                    for c in r.get("context", [])],
    })


class Handler(BaseHTTPRequestHandler):
    server_version = "JARVIS/1.0"
    timeout = 30  # seconds: a stalled client can't hold a thread forever

    def log_message(self, fmt, *args):
        sys.stderr.write("[web] " + (fmt % args) + "\n")

    # ------------------------------------------------------------------ helpers
    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(clean(body), default=str, allow_nan=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _same_origin(self):
        """Block other websites (and DNS-rebinding pages) from using this local API / the LLM key."""
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        host = (self.headers.get("Host") or "").lower()
        if host not in allowed:
            return False
        origin = self.headers.get("Origin")
        if origin and origin.lower() not in {f"http://{h}" for h in allowed}:
            return False
        return True

    def _json_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("bad Content-Length")
        if n < 0 or n > MAX_BODY:
            raise ValueError("request too large")
        data = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    # ------------------------------------------------------------------ routes
    def do_GET(self):
        if not self._same_origin():
            return self._send(403, {"error": "forbidden host"})
        path = self.path.split("?", 1)[0]
        if path == "/api/meta":
            tools = [{"name": t["function"]["name"], "description": t["function"]["description"]}
                     for t in TOOLS]
            return self._send(200, {"model": MODEL, "tools": tools, "graph": GRAPH_INFO,
                                    "languages": Bodhan.LANGUAGES})
        if path == "/api/graph":
            return self._send(200, GRAPH_OUTLINE)
        if path in ("", "/"):  # the Mahabharata voyage is the front door; the original UI lives at /classic
            path = "/voyage.html" if (WEB / "voyage.html").is_file() else "/index.html"
        elif path in ("/classic", "/classic/"):
            path = "/index.html"
        base = WEB
        if path.startswith("/bodhan/"):  # Bodhan/web/* (language picker, mic, auto-speak)
            base, path = ROOT / "Bodhan" / "web", path[len("/bodhan"):]
        try:
            target = (base / path.lstrip("/")).resolve()
            if base not in target.parents or not target.is_file():
                return self._send(404, {"error": "not found"})
            data = target.read_bytes()
        except (OSError, ValueError):
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("json") or ctype.endswith("+xml"):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._same_origin():
            return self._send(403, {"error": "forbidden origin"})
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self._send(415, {"error": "Content-Type must be application/json"})
        try:
            body = self._json_body()
        except Exception as e:
            return self._send(400, {"error": f"bad request: {e}"})

        if path == "/api/chart":
            query = body.get("query")
            if not isinstance(query, str) or not query.strip():
                return self._send(400, {"error": "query must be a non-empty string"})
            query = query.strip()
            lang = body.get("lang") or "en"
            if lang not in Bodhan.LANGUAGES:
                return self._send(400, {"error": f"unsupported language: {lang}"})
            t0 = time.perf_counter()
            try:
                english = Bodhan.query_to_english(query, lang)          # 1) Telugu/Hindi -> English
                results = make_charts(english)                           # 2) knowledge graph + LLM
                if lang != "en":                                         # 3) answer -> query language
                    for r in results:
                        if not r.get("tool") and r.get("text"):
                            r["text"] = Bodhan.answer_to_language(r["text"], lang)
            except Bodhan.BodhanError as e:
                traceback.print_exc()
                return self._send(502, {"error": f"Bodhan: {e}"})
            except Exception as e:  # Groq/network/auth errors
                traceback.print_exc()
                return self._send(502, {"error": f"{type(e).__name__}: {e}"})
            return self._send(200, {"model": MODEL, "query": query, "lang": lang,
                                    "english_query": english if lang != "en" else None,
                                    "elapsed_ms": round((time.perf_counter() - t0) * 1000),
                                    "retrieval": retrieval_json(results),
                                    "results": serialize(results)})

        if path == "/api/bodhan/transcribe":  # {"audio": <base64 WAV>, "lang": "te"} -> {"text": ...}
            lang = body.get("lang") or "en"
            try:
                audio = base64.b64decode(body.get("audio") or "", validate=True)
                if not audio:
                    raise ValueError("no audio")
                return self._send(200, {"text": Bodhan.transcribe(audio, lang), "lang": lang})
            except (ValueError, binascii.Error) as e:
                return self._send(400, {"error": f"bad audio: {e}"})
            except Bodhan.BodhanError as e:
                return self._send(502, {"error": f"Bodhan: {e}"})

        if path == "/api/bodhan/speak":  # {"text": "...", "lang": "te"} -> saved audio/wav
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                return self._send(400, {"error": "text must be a non-empty string"})
            try:
                lang = body.get("lang") or "en"
                audio = Bodhan.speak(text, lang)
            except Bodhan.BodhanError as e:
                return self._send(502, {"error": f"Bodhan: {e}"})
            audio_dir = ROOT / "Bodhan" / "Audio"
            filename = f"answer_{lang}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.wav"
            try:
                audio_dir.mkdir(parents=True, exist_ok=True)
                (audio_dir / filename).write_bytes(audio)
            except OSError as e:
                traceback.print_exc()
                return self._send(500, {"error": f"could not save generated audio: {e}"})
            self.log_message("saved Bodhan speech audio to %s", audio_dir / filename)
            return self._send(200, audio, "audio/wav")

        if path == "/api/render":
            tool, args = body.get("tool"), body.get("args") or {}
            if not isinstance(args, dict):
                return self._send(400, {"error": "args must be an object"})
            try:
                fig, err = build_figure(tool, args), None
            except Exception as e:
                fig, err = None, f"{type(e).__name__}: {e}"
            return self._send(200, {"model": MODEL, "elapsed_ms": 0,
                                    "results": serialize([{"tool": tool, "args": args,
                                                           "figure": fig, "error": err}])})

        self._send(404, {"error": "not found"})


def main():
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"JARVIS UI running at {url}  (model: {MODEL})  — Ctrl+C to stop")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
