"""Direct video links: checks on the link, downloads from a local test server, and jobs started from a link."""

import asyncio
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db, download
from app.audio import MediaError
from app.config import settings
from app.main import app
from tests.test_audio import make_video

PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 0))]  # what getaddrinfo returns for a public host
PRIVATE = [(2, 1, 6, "", ("10.0.0.5", 0))]


class LinkCheckTests(unittest.TestCase):
    def test_only_http_links(self):
        for url in ("ftp://example.com/v.mp4", "file:///C:/v.mp4", "example.com/v.mp4", "javascript:alert(1)"):
            with self.assertRaises(MediaError, msg=url):
                download.check_link(url)

    def test_private_and_local_addresses_are_refused(self):
        for url in ("http://127.0.0.1:8000/api/health", "http://localhost/v.mp4", "http://[::1]/v.mp4"):
            with self.assertRaises(MediaError, msg=url) as caught:
                download.check_link(url)
            self.assertIn("private network", str(caught.exception))
        with patch("socket.getaddrinfo", return_value=PRIVATE), self.assertRaises(MediaError):
            download.check_link("https://intranet.example.com/v.mp4")

    def test_video_sites_are_refused(self):
        with patch("socket.getaddrinfo", return_value=PUBLIC):
            for url in ("https://www.youtube.com/watch?v=abc", "https://youtu.be/abc", "https://vimeo.com/123", "https://m.facebook.com/watch/?v=1"):
                with self.assertRaises(MediaError, msg=url) as caught:
                    download.check_link(url)
                self.assertIn("video site", str(caught.exception))
            download.check_link("https://cdn.example.com/talk.mp4")  # no error
            self.assertFalse(download.is_site("https://notyoutube.com/v.mp4"))

    def test_name_from_link(self):
        self.assertEqual(download.name_from_link("https://cdn.example.com/media/My%20Talk.mp4?sig=1"), "My Talk.mp4")
        self.assertEqual(download.name_from_link("https://www.youtube.com/watch?v=abc"), "www.youtube.com")


class _Server(BaseHTTPRequestHandler):
    video = b""

    def do_GET(self):
        routes = {
            "/talk.mp4": (200, "video/mp4", self.video, None),
            "/noext": (200, "video/mp4", self.video, None),
            "/page": (200, "text/html; charset=utf-8", b"<html>not a video</html>", None),
            "/uc?id=1": (200, "application/octet-stream", self.video, None),  # named in Content-Disposition, like cloud storage
            "/uc?id=2": (200, "application/octet-stream", self.video, None),
            "/blob": (200, "application/octet-stream", self.video, None),  # binary with no name: can't tell it's a video
            "/missing.mp4": (404, "text/plain", b"no", None),
            "/huge.mp4": (200, "video/mp4", b"x" * 10, str(500 * 1024 * 1024)),
        }
        if self.path == "/wiki.webm":  # like Wikimedia: refuses a library's default User-Agent
            ok = self.headers.get("User-Agent", "").startswith("SubtitleGenerator/")
            routes[self.path] = (200, "video/webm", self.video, None) if ok else (403, "text/plain", b"no", None)
        status, kind, body, length = routes.get(self.path, (404, "text/plain", b"no", None))
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", length or str(len(body)))
        if self.path == "/uc?id=1":
            self.send_header("Content-Disposition", 'attachment; filename="My Talk.mp4"')
        elif self.path == "/uc?id=2":
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%E0%A4%95%E0%A4%95%E0%A5%8D%E0%A4%B7%E0%A4%BE.mov")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # same signature as the base class
        pass


class LocalServerCase(unittest.TestCase):
    """A local web server with a video, a web page and error responses; no tests of its own."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        video = os.path.join(cls.tmp, "talk.mp4")
        make_video(video, seconds=2)
        with open(video, "rb") as f:
            _Server.video = f.read()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Server)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        # The test server is on this computer, which real links may not reach
        self.allow_local = patch("app.download._require_public")
        self.allow_local.start()
        self.addCleanup(self.allow_local.stop)

    def fetch(self, path):
        progress = []
        result = asyncio.run(download.download(self.base + path, self.workdir, progress.append))
        return result, progress


class DirectDownloadTests(LocalServerCase):
    def test_video_file_is_saved(self):
        (path, title), progress = self.fetch("/talk.mp4")
        self.assertEqual(title, "talk.mp4")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), _Server.video)
        self.assertEqual(progress[-1], 100)

    def test_hosts_that_want_a_user_agent_accept_ours(self):
        (path, _), _ = self.fetch("/wiki.webm")
        self.assertTrue(os.path.getsize(path) > 0)

    def test_video_without_extension_is_accepted_by_its_type(self):
        (path, _), _ = self.fetch("/noext")
        self.assertTrue(path.endswith(".mp4"))

    def test_file_named_only_in_its_headers_is_accepted(self):
        (path, name), _ = self.fetch("/uc?id=1")
        self.assertEqual((name, os.path.splitext(path)[1]), ("My Talk.mp4", ".mp4"))
        (path, name), _ = self.fetch("/uc?id=2")
        self.assertEqual((name, os.path.splitext(path)[1]), ("कक्षा.mov", ".mov"))

    def test_unnamed_binary_is_refused(self):
        with self.assertRaises(MediaError):
            self.fetch("/blob")

    def test_web_page_is_refused(self):
        with self.assertRaises(MediaError) as caught:
            self.fetch("/page")
        self.assertIn("web page", str(caught.exception))

    def test_errors_and_size_limit(self):
        with self.assertRaises(MediaError) as caught:
            self.fetch("/missing.mp4")
        self.assertIn("404", str(caught.exception))
        with self.assertRaises(MediaError) as caught:
            self.fetch("/huge.mp4")
        self.assertIn("larger than", str(caught.exception))

    def test_network_errors_have_a_readable_message(self):
        import httpx

        for error, expected in ((httpx.ConnectTimeout(""), "took too long"), (httpx.ConnectError(""), "ConnectError")):
            with patch("app.download._fetch", side_effect=error), self.assertRaises(MediaError) as caught:
                self.fetch("/talk.mp4")
            self.assertIn(expected, str(caught.exception))

    def test_private_address_reached_after_the_dns_check_is_refused(self):
        # DNS rebinding: the name looked public when checked, but the connection lands on this computer
        self.allow_local.stop()
        self.addCleanup(self.allow_local.start)
        with patch("app.download._check_host"), self.assertRaises(MediaError) as caught:
            self.fetch("/talk.mp4")
        self.assertIn("private network", str(caught.exception))

    def test_redirect_to_a_private_address_is_refused(self):
        self.allow_local.stop()
        self.addCleanup(self.allow_local.start)  # setUp's cleanup stops it again
        with self.assertRaises(MediaError):
            self.fetch("/talk.mp4")


class LinkJobApiTests(LocalServerCase):
    """Whole jobs from a link, in mock mode, against the same local server."""

    def setUp(self):
        super().setUp()
        database = os.path.join(self.workdir, "test.db").replace("\\", "/")
        overrides = {"dev_mock_mode": True, "database_url": f"sqlite:///{database}", "direct_video_links": True}
        for p in [patch.object(settings, name, value) for name, value in overrides.items()]:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: [engine.dispose() for engine in db._engines.values()])
        self.client = TestClient(app)

    def post(self, url, **body):
        return self.client.post("/api/jobs/url", json={"url": url, "language": "hi", **body})

    def test_job_from_a_direct_link(self):
        started = self.post(self.base + "/talk.mp4", target_language="en")
        self.assertEqual(started.status_code, 202, started.text)
        job = self.client.get(f"/api/jobs/{started.json()['id']}").json()
        self.assertEqual((job["status"], job["source"], job["file_name"]), ("done", "url", "talk.mp4"), job["error"])
        self.assertEqual(job["source_url"], self.base + "/talk.mp4")
        self.assertTrue(job["original_srt"] and job["translated_srt"])

    def test_links_can_be_turned_off(self):
        with patch.object(settings, "direct_video_links", False):
            response = self.post(self.base + "/talk.mp4")
            self.assertFalse(self.client.get("/api/health").json()["direct_video_links"])
        self.assertEqual(response.status_code, 403)
        self.assertIn("turned off", response.json()["detail"])
        self.assertEqual(self.client.get("/api/jobs").json()["jobs"], [])

    def test_video_site_links_are_refused(self):
        with patch("socket.getaddrinfo", return_value=PUBLIC):
            refused = self.post("https://vimeo.com/123")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("video site", refused.json()["detail"])

    def test_job_name_comes_from_the_server_when_the_link_has_none(self):
        job = self.client.get(f"/api/jobs/{self.post(self.base + '/uc?id=1').json()['id']}").json()
        self.assertEqual((job["status"], job["file_name"]), ("done", "My Talk.mp4"), job["error"])

    def test_failed_download_fails_the_job(self):
        job = self.client.get(f"/api/jobs/{self.post(self.base + '/page').json()['id']}").json()
        self.assertEqual(job["status"], "failed")
        self.assertIn("web page", job["error"])

    def test_bad_links_are_refused_before_a_job_exists(self):
        self.allow_local.stop()
        self.addCleanup(self.allow_local.start)
        self.assertEqual(self.post("http://127.0.0.1:8000/api/health").status_code, 400)
        self.assertEqual(self.post("not a link").status_code, 400)
        self.assertEqual(self.client.get("/api/jobs").json()["jobs"], [])


if __name__ == "__main__":
    unittest.main()
