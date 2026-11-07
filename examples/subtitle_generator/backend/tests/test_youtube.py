"""YouTube links: reading the video id, the checks before downloading, and whole jobs. yt-dlp is replaced by a fake (offline)."""

import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from yt_dlp.utils import DownloadError

from app import db, youtube
from app.audio import MediaError
from app.config import settings
from app.main import app
from tests.test_audio import make_video

VID = "aqz-KE-bpKQ"
WATCH = f"https://www.youtube.com/watch?v={VID}"


class FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL: returns `info`, and 'downloads' by writing a small test video."""

    info: dict = {}
    error: str | None = None
    calls: list = []

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        FakeYoutubeDL.calls.append(("info", url, self.options["allowed_extractors"]))
        if FakeYoutubeDL.error:
            raise DownloadError(FakeYoutubeDL.error)
        return {"title": "Big Buck Bunny", "duration": 3, "live_status": "not_live", **FakeYoutubeDL.info}

    def process_ie_result(self, info, download=True):
        FakeYoutubeDL.calls.append(("download",))
        path = self.options["outtmpl"]["default"].replace("%(ext)s", "mp4")
        make_video(path)
        for hook in self.options["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
        return {**info, "requested_downloads": [{"filepath": path}]}


class FakeCase(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        FakeYoutubeDL.info, FakeYoutubeDL.error, FakeYoutubeDL.calls = {}, None, []
        fake = patch("yt_dlp.YoutubeDL", FakeYoutubeDL)
        fake.start()
        self.addCleanup(fake.stop)


class VideoIdTests(unittest.TestCase):
    def test_links_to_one_video(self):
        for url in (
            WATCH,
            f"https://youtube.com/watch?v={VID}&t=42s",
            f"https://m.youtube.com/watch?v={VID}",
            f"https://music.youtube.com/watch?v={VID}",
            f"https://youtu.be/{VID}?si=abc",
            f"https://www.youtube.com/shorts/{VID}",
            f"https://www.youtube.com/live/{VID}",
            f"https://www.youtube-nocookie.com/embed/{VID}",
            f"https://www.youtube.com/watch?v={VID}&list=PL123",  # a video in a playlist: just that video
        ):
            self.assertEqual(youtube.video_id(url), VID, url)

    def test_other_links(self):
        for url in (
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/@channel",
            "https://www.youtube.com/watch?v=short",
            "https://notyoutube.com/watch?v=" + VID,
            "https://example.com/talk.mp4",
            "ftp://youtube.com/watch?v=" + VID,
        ):
            self.assertIsNone(youtube.video_id(url), url)
        self.assertTrue(youtube.is_youtube("https://www.youtube.com/playlist?list=PL123"))
        self.assertFalse(youtube.is_youtube("https://notyoutube.com/x"))

    def test_check_link_gives_the_clean_watch_link(self):
        self.assertEqual(youtube.check_link(f"https://youtu.be/{VID}?si=tracking"), WATCH)
        with self.assertRaises(MediaError) as caught:
            youtube.check_link("https://www.youtube.com/playlist?list=PL123")
        self.assertIn("Playlists", str(caught.exception))


class DownloadTests(FakeCase):
    def fetch(self):
        progress = []
        return asyncio.run(youtube.download(f"https://youtu.be/{VID}", self.workdir, progress.append)), progress

    def test_audio_is_downloaded_with_only_the_youtube_video_handler(self):
        (path, title), progress = self.fetch()
        self.assertTrue(os.path.exists(path))
        self.assertEqual(title, "Big Buck Bunny")
        self.assertEqual(progress, [0, 50])
        self.assertEqual(FakeYoutubeDL.calls[0], ("info", WATCH, ["youtube"]))

    def test_long_videos_are_refused_before_downloading(self):
        FakeYoutubeDL.info = {"duration": settings.max_video_seconds + 60}
        with self.assertRaises(MediaError) as caught:
            self.fetch()
        self.assertIn("minutes long", str(caught.exception))
        self.assertNotIn(("download",), FakeYoutubeDL.calls)

    def test_live_streams_are_refused(self):
        for status in ("is_live", "is_upcoming"):
            FakeYoutubeDL.info = {"live_status": status, "duration": None}
            with self.assertRaises(MediaError, msg=status) as caught:
                self.fetch()
            self.assertIn("Live streams", str(caught.exception))
        self.assertNotIn(("download",), FakeYoutubeDL.calls)

    def test_youtube_errors_have_a_readable_message(self):
        for error, expected in (
            (f"ERROR: [youtube] {VID}: Private video. Sign in if you've been granted access", "private"),
            (f"ERROR: [youtube] {VID}: Sign in to confirm you’re not a bot", "isn't a bot"),
            (f"ERROR: [youtube] {VID}: Sign in to confirm your age", "age-restricted"),
            (f"ERROR: [youtube] {VID}: Something new", "YouTube download failed: Something new"),
        ):
            FakeYoutubeDL.error = error
            with self.assertRaises(MediaError, msg=error) as caught:
                self.fetch()
            self.assertIn(expected, str(caught.exception))


class YoutubeJobApiTests(FakeCase):
    """Whole jobs from a YouTube link, in mock mode."""

    def setUp(self):
        super().setUp()
        database = os.path.join(self.workdir, "test.db").replace("\\", "/")
        overrides = {"dev_mock_mode": True, "database_url": f"sqlite:///{database}", "youtube_links": True, "direct_video_links": False}
        for p in [patch.object(settings, name, value) for name, value in overrides.items()]:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: [engine.dispose() for engine in db._engines.values()])
        self.client = TestClient(app)

    def post(self, url, **body):
        return self.client.post("/api/jobs/url", json={"url": url, "language": "en", "allow_youtube": True, **body})

    def test_job_from_a_youtube_link(self):
        started = self.post(f"https://youtu.be/{VID}?si=tracking", target_language="hi")
        self.assertEqual(started.status_code, 202, started.text)
        job = self.client.get(f"/api/jobs/{started.json()['id']}").json()
        self.assertEqual((job["status"], job["source"], job["file_name"], job["source_url"]), ("done", "youtube", "Big Buck Bunny", WATCH), job["error"])
        self.assertTrue(job["original_srt"] and job["translated_srt"])

    def test_health_reports_both_switches(self):
        health = self.client.get("/api/health").json()
        self.assertEqual((health["youtube_links"], health["direct_video_links"]), (True, False))

    def test_youtube_links_can_be_turned_off(self):
        with patch.object(settings, "youtube_links", False):
            response = self.post(WATCH)
        self.assertEqual(response.status_code, 403)
        self.assertIn("YouTube links are turned off", response.json()["detail"])

    def test_the_user_must_allow_youtube_links(self):
        response = self.post(WATCH, allow_youtube=False)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Allow YouTube links", response.json()["detail"])

    def test_switches_are_separate(self):
        # YouTube on, direct links off: a direct link is still refused
        response = self.post("https://example.com/talk.mp4")
        self.assertEqual(response.status_code, 403)
        self.assertIn("Direct video links are turned off", response.json()["detail"])

    def test_playlists_are_refused_before_a_job_exists(self):
        self.assertEqual(self.post("https://www.youtube.com/playlist?list=PL123").status_code, 400)
        self.assertEqual(self.client.get("/api/jobs").json()["jobs"], [])

    def test_failed_download_fails_the_job(self):
        FakeYoutubeDL.error = f"ERROR: [youtube] {VID}: Video unavailable"
        job = self.client.get(f"/api/jobs/{self.post(WATCH).json()['id']}").json()
        self.assertEqual((job["status"], job["error"]), ("failed", "This video is unavailable. Check the link."))


if __name__ == "__main__":
    unittest.main()
