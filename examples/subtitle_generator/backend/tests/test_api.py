"""The HTTP API end to end, with DEV_MOCK_MODE so no key or network is needed, and a throwaway database."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.main import app, serve_web_app
from tests.test_audio import make_video


def stored(job_id: str) -> db.Job:
    """The job as saved in the database."""
    job = db.get_job(job_id)
    assert job is not None
    return job


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        database = os.path.join(self.tmp, "test.db").replace("\\", "/")
        self.patches = [patch.object(settings, "dev_mock_mode", True), patch.object(settings, "database_url", f"sqlite:///{database}")]
        for p in self.patches:
            p.start()
        self.client = TestClient(app)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        for engine in db._engines.values():
            engine.dispose()  # release the SQLite file so Windows can delete it
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, path, **form):
        with open(path, "rb") as f:
            return self.client.post("/api/jobs", data=form, files={"video": (os.path.basename(path), f, "video/mp4")})

    def run_job(self, path, **form):
        """Start a job and return it finished (TestClient runs background tasks before returning)."""
        started = self.post(path, **form)
        self.assertEqual(started.status_code, 202, started.text)
        return self.client.get(f"/api/jobs/{started.json()['id']}").json()

    def video(self, name="v.mp4", **kwargs):
        path = os.path.join(self.tmp, name)
        make_video(path, **kwargs)
        return path

    def test_built_web_app_can_be_served_with_the_api(self):
        # The Docker image serves frontend/dist at / ; API routes registered first keep working
        from fastapi import FastAPI

        dist = os.path.join(self.tmp, "dist")
        os.makedirs(dist)
        with open(os.path.join(dist, "index.html"), "w", encoding="utf-8") as f:
            f.write("<title>Subtitle Generator</title>")
        site = FastAPI()
        site.get("/api/health")(lambda: {"ok": True})
        serve_web_app(site, dist)
        client = TestClient(site)
        self.assertIn("Subtitle Generator", client.get("/").text)
        self.assertEqual(client.get("/api/health").json(), {"ok": True})

    def test_health(self):
        data = self.client.get("/api/health").json()
        self.assertTrue(data["ffmpeg_found"])
        self.assertTrue(data["dev_mock_mode"])

    def test_original_and_translated_subtitles(self):
        job = self.run_job(self.video(seconds=30), language="hi", target_language="en")
        self.assertEqual((job["status"], job["progress"], job["error"]), ("done", 100, None))
        self.assertGreater(len(job["segments"]), 1)
        self.assertTrue(job["original_srt"].startswith("1\n00:00:00,000 --> "))
        # Translated lines keep the original timings
        self.assertEqual([(s["start"], s["end"]) for s in job["translated_segments"]], [(s["start"], s["end"]) for s in job["segments"]])
        self.assertTrue(job["translated_segments"][0]["text"].startswith("[en] "))

    def test_same_language_skips_translation(self):
        job = self.run_job(self.video(seconds=1), language="hi", target_language="hi")
        self.assertEqual(job["status"], "done")
        self.assertIsNone(job["translated_srt"])

    def test_history_lists_jobs_newest_first_without_subtitles(self):
        self.run_job(self.video("first.mp4", seconds=1), language="hi")
        self.run_job(self.video("second.mp4", seconds=1), language="ta", target_language="en")
        history = self.client.get("/api/jobs").json()["jobs"]
        self.assertEqual([(j["file_name"], j["language"], j["target_language"]) for j in history], [("second.mp4", "ta", "en"), ("first.mp4", "hi", "")])
        self.assertNotIn("segments", history[0])
        self.assertTrue(history[0]["created_at"].endswith(("Z", "+00:00")))

    def test_history_comes_in_pages(self):
        for i in range(12):
            db.add_job(db.Job(file_name=f"v{i:02d}.mp4", language="hi", status="done"))
        first = self.client.get("/api/jobs").json()
        self.assertEqual((len(first["jobs"]), first["total"]), (10, 12))  # 10 per page by default
        self.assertEqual(first["jobs"][0]["file_name"], "v11.mp4")  # newest first
        second = self.client.get("/api/jobs", params={"limit": 10, "offset": 10}).json()
        self.assertEqual([j["file_name"] for j in second["jobs"]], ["v01.mp4", "v00.mp4"])
        self.assertEqual(self.client.get("/api/jobs", params={"limit": 0}).status_code, 422)
        self.assertEqual(self.client.get("/api/jobs", params={"limit": 500}).status_code, 422)

    def test_unknown_job(self):
        self.assertEqual(self.client.get("/api/jobs/nope").status_code, 404)

    def test_failed_job_keeps_the_error(self):
        with patch("app.jobs.bodhan.transcribe", side_effect=RuntimeError("boom")), self.assertLogs("subtitle_generator", "ERROR"):
            job = self.run_job(self.video(seconds=1), language="hi")
        self.assertEqual(job["status"], "failed")
        self.assertIn("boom", job["error"])

    def test_one_job_at_a_time(self):
        running = db.add_job(db.Job(file_name="first.mp4", language="hi", status="running"))
        response = self.post(self.video(seconds=1), language="hi")
        self.assertEqual(response.status_code, 409)
        self.assertIn("still being processed", response.json()["detail"])
        self.assertEqual([j["id"] for j in self.client.get("/api/jobs").json()["jobs"]], [running.id])

    def test_only_one_of_simultaneous_starts_wins(self):
        # Check-and-start is one step: 8 threads starting jobs at the same instant give exactly one job
        import threading

        from fastapi import HTTPException

        from app.main import _start_one

        barrier, outcomes = threading.Barrier(8), []

        def start(i):
            barrier.wait()
            try:
                _start_one(lambda: db.add_job(db.Job(file_name=f"{i}.mp4", language="hi", status="running")))
                outcomes.append("started")
            except HTTPException as exc:
                outcomes.append(exc.status_code)

        threads = [threading.Thread(target=start, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual((outcomes.count("started"), outcomes.count(409)), (1, 7))

    def test_unfinished_jobs_fail_after_a_restart(self):
        transcribing = db.add_job(db.Job(file_name="a.mp4", language="hi", target_language="en", status="running"))
        translating = db.add_job(
            db.Job(file_name="b.mp4", language="hi", target_language="en", status="running", segments=[{"start": 0.0, "end": 1.0, "text": "नमस्ते"}])
        )
        db.add_job(db.Job(source="youtube", source_url="https://www.youtube.com/watch?v=aqz-KE-bpKQ", file_name="c", language="en", status="running"))
        db.fail_unfinished_jobs()
        jobs = {j["file_name"]: j for j in self.client.get("/api/jobs").json()["jobs"]}
        self.assertEqual((jobs["a.mp4"]["status"], jobs["a.mp4"]["resumable"]), ("failed", False))
        self.assertIn("Choose the video again", jobs["a.mp4"]["error"])
        self.assertIn("Run again", jobs["c"]["error"])  # a link job starts again from its link
        self.assertEqual((jobs["b.mp4"]["status"], jobs["b.mp4"]["resumable"]), ("failed", True))
        self.assertIn("resume", jobs["b.mp4"]["error"])
        self.assertEqual(stored(transcribing.id).status, "failed")
        self.assertEqual(stored(translating.id).status, "failed")

    def test_translation_failure_keeps_the_subtitles_and_resume_translates_only_missing_lines(self):
        calls = []

        async def flaky(client, text, target):
            calls.append(text)
            if len(calls) > 1:
                raise RuntimeError("translation down")
            return f"[{target}] {text}"

        with patch("app.jobs.bodhan.translate", side_effect=flaky), self.assertLogs("subtitle_generator", "ERROR"):
            job = self.run_job(self.video(seconds=30), language="hi", target_language="en")
        self.assertEqual((job["status"], job["resumable"]), ("failed", True))
        self.assertIn("translation down", job["error"])
        lines = len(job["segments"])
        self.assertGreater(lines, 1)
        self.assertIsNotNone(job["original_srt"])
        self.assertIsNone(job["translated_srt"])  # not shown until complete
        saved = sum(line is not None for line in stored(job["id"]).translated_segments or [])
        self.assertEqual(saved, 1)

        calls.clear()

        async def working(client, text, target):
            calls.append(text)
            return f"[{target}] {text}"

        with patch("app.jobs.bodhan.translate", side_effect=working):
            resumed = self.client.post(f"/api/jobs/{job['id']}/resume")
        self.assertEqual(resumed.status_code, 202)
        job = self.client.get(f"/api/jobs/{job['id']}").json()
        self.assertEqual((job["status"], job["error"], job["resumable"]), ("done", None, False))
        self.assertEqual(len(calls), lines - saved)  # finished lines aren't translated again
        self.assertEqual([(s["start"], s["end"]) for s in job["translated_segments"]], [(s["start"], s["end"]) for s in job["segments"]])
        self.assertTrue(all(s["text"].startswith("[en] ") for s in job["translated_segments"]))

    def test_editing_a_line_keeps_its_timing_and_updates_the_srt(self):
        job = self.run_job(self.video(seconds=30), language="hi", target_language="en")
        url = f"/api/jobs/{job['id']}/lines"
        edited = self.client.patch(url, json={"track": "original", "index": 1, "text": "  सही   किया गया\nवाक्य "})
        self.assertEqual(edited.status_code, 200, edited.text)
        edited = edited.json()
        self.assertEqual(edited["segments"][1]["text"], "सही किया गया वाक्य")
        self.assertEqual((edited["segments"][1]["start"], edited["segments"][1]["end"]), (job["segments"][1]["start"], job["segments"][1]["end"]))
        self.assertIn("सही किया गया वाक्य", edited["original_srt"])
        self.assertEqual(edited["translated_segments"], job["translated_segments"])  # the other track is untouched

        self.client.patch(url, json={"track": "translated", "index": 0, "text": "Fixed line"})
        saved = self.client.get(f"/api/jobs/{job['id']}").json()  # kept in the database
        self.assertEqual(saved["translated_segments"][0]["text"], "Fixed line")
        self.assertEqual(saved["segments"][1]["text"], "सही किया गया वाक्य")

        self.assertEqual(self.client.patch(url, json={"track": "original", "index": 1, "text": "   "}).status_code, 400)
        self.assertEqual(self.client.patch(url, json={"track": "original", "index": 999, "text": "x"}).status_code, 400)
        self.assertEqual(self.client.patch(url, json={"track": "other", "index": 0, "text": "x"}).status_code, 422)
        self.assertEqual(self.client.patch("/api/jobs/nope/lines", json={"track": "original", "index": 0, "text": "x"}).status_code, 404)

    def test_no_editing_while_a_job_runs_or_without_that_track(self):
        untranslated = self.run_job(self.video(seconds=1), language="hi")
        response = self.client.patch(f"/api/jobs/{untranslated['id']}/lines", json={"track": "translated", "index": 0, "text": "b"})
        self.assertEqual(response.status_code, 400)
        running = db.add_job(db.Job(file_name="a.mp4", language="hi", status="running", segments=[{"start": 0.0, "end": 1.0, "text": "a"}]))
        response = self.client.patch(f"/api/jobs/{running.id}/lines", json={"track": "original", "index": 0, "text": "b"})
        self.assertEqual(response.status_code, 409)

    def test_only_stopped_translations_can_be_resumed(self):
        done = self.run_job(self.video(seconds=1), language="hi", target_language="en")
        self.assertEqual(self.client.post(f"/api/jobs/{done['id']}/resume").status_code, 400)
        no_text = db.add_job(db.Job(file_name="v.mp4", language="hi", target_language="en", status="failed"))
        self.assertEqual(self.client.post(f"/api/jobs/{no_text.id}/resume").status_code, 400)
        self.assertEqual(self.client.post("/api/jobs/nope/resume").status_code, 404)

    def test_resume_waits_for_the_running_job(self):
        stopped = db.add_job(
            db.Job(file_name="a.mp4", language="hi", target_language="en", status="failed", segments=[{"start": 0.0, "end": 1.0, "text": "नमस्ते"}])
        )
        db.add_job(db.Job(file_name="b.mp4", language="hi", status="running"))
        self.assertEqual(self.client.post(f"/api/jobs/{stopped.id}/resume").status_code, 409)

    def test_rejected_uploads(self):
        bad_type = os.path.join(self.tmp, "notes.txt")
        empty = os.path.join(self.tmp, "empty.mp4")
        open(bad_type, "w").close()
        open(empty, "w").close()
        self.assertEqual(self.post(bad_type, language="hi").status_code, 400)
        self.assertEqual(self.post(empty, language="hi").status_code, 400)
        silent = self.post(self.video(has_audio=False), language="hi")
        self.assertEqual(silent.status_code, 400)
        self.assertIn("no audio", silent.json()["detail"])
        self.assertEqual(self.post(self.video(), target_language="en").status_code, 422)  # language is required
        # Refused uploads don't become jobs
        self.assertEqual(self.client.get("/api/jobs").json()["jobs"], [])

    def test_too_long(self):
        with patch.object(settings, "max_video_seconds", 2):
            response = self.post(self.video(seconds=4), language="hi")
        self.assertEqual(response.status_code, 400)
        self.assertIn("limit", response.json()["detail"])

    def test_missing_key(self):
        keys = {"dev_mock_mode": False, "bodhan_api_key": "", "bodhan_transcribe_api_key": "", "bodhan_translate_api_key": ""}
        with patch.multiple(settings, **keys):
            response = self.post(self.video(seconds=1), language="hi")
        self.assertEqual(response.status_code, 503)
        self.assertIn("BODHAN_TRANSCRIBE_API_KEY", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
