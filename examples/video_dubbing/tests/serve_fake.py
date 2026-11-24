"""Dev helper: serve the fake Bodhan API over real HTTP so the web UI can be exercised without paid keys.
    python tests/serve_fake.py 9100   then   BODHAN_BASE_URL=http://127.0.0.1:9100 BODHAN_API_KEY=x python -m bodhan_dub.web
Test double only; the pipeline never falls back to it."""
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from fake_bodhan import FakeBodhan

fake = FakeBodhan()


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        r = fake.handle(SimpleNamespace(url=SimpleNamespace(path=self.path), content=body))
        self.send_response(r.status_code)
        self.send_header("content-type", r.headers.get("content-type", "application/json"))
        self.send_header("content-length", str(len(r.content)))
        self.end_headers()
        self.wfile.write(r.content)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
