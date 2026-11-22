import io
import json
import unittest
import wave
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from bodhan_agent.planner import ChatPlanner
from bodhan_agent.run import run_task
from bodhan_mcp.transcription import SAMPLE_RATE


class SubtitleTests(unittest.TestCase):
    def run_subtitle_task(self, *, target="en", approved=True, budget="1", window_seconds=None, steps=4,
                          throttle_window=None):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "voice-note.wav"
        with wave.open(str(source), "wb") as wav:
            wav.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
            for sample in range(1, 5):
                wav.writeframes(sample.to_bytes(2, "little") * (8 * SAMPLE_RATE))
            wav.writeframes(b"\x05\x00" * (SAMPLE_RATE // 4))

        actions = iter([
            {"action": "declare_deliverables", "deliverables": [
                {"description": "Subtitles", "format": "srt", "language": target},
            ]},
            {"action": "transcribe", "source": "input-1", "language": "hi", "window_seconds": window_seconds},
            {"action": "save_subtitles", "source": "text-1", "deliverable": "deliverable-1"},
            {"action": "finish", "status": "completed"},
        ])
        texts = iter(["कक्षा कल है।", "", "सभी छात्र आएं।", "कक्षा कल है।", "धन्यवाद।"])
        translations = {
            "कक्षा कल है।": "Class is tomorrow.",
            "सभी छात्र आएं।": "All students should come.",
            "धन्यवाद।": "Thank you.",
        }
        transcribed_frames = []
        translation_inputs = []
        reviews = []

        def planner_reply(request):
            system, user = json.loads(request.content)["messages"]
            if "Check whether" in system["content"]:
                pair = json.loads(user["content"])
                sources = json.loads(pair["source"])
                targets = json.loads(pair["translation"])
                self.assertEqual(targets, {cue: translations[text] for cue, text in sources.items()})
                reviews.append(list(sources.values()))
                answer = {"approved": approved, "explanation": "Test review."}
            else:
                answer = dict(next(actions), reason="Advance the subtitle task.")
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

        def bodhan_reply(_client, url, **kwargs):
            if url.endswith("/audio/transcriptions"):
                self.assertEqual(kwargs["data"]["language"], "hi")
                with wave.open(io.BytesIO(kwargs["files"]["file"][1]), "rb") as window:
                    transcribed_frames.append(window.getnframes())
                if len(transcribed_frames) == throttle_window:
                    return httpx.Response(429, headers={"Retry-After": "120"})
                return httpx.Response(200, json={"text": next(texts)})
            request = kwargs["json"]
            self.assertEqual(request["model"], "indic-translate")
            text = request["messages"][0]["content"]
            translation_inputs.append(text)
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": translations[text]}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            })

        environment = {
            "LLM_BASE_URL": "https://planner.example/v1", "LLM_MODEL": "test-planner",
            "LLM_API_KEY": "test-key", "BODHAN_TRANSCRIBE_KEY": "test-key",
            "BODHAN_TRANSLATE_KEY": "test-key",
        }
        output = root / "run"
        with (
            patch.dict("os.environ", environment),
            httpx.Client(transport=httpx.MockTransport(planner_reply)) as client,
            patch.object(httpx.Client, "post", autospec=True, side_effect=bodhan_reply),
            patch("bodhan_mcp.transcription._normalize", side_effect=lambda src, dest: dest.write_bytes(src.read_bytes())),
        ):
            client.post = lambda url, **kwargs: client.request("POST", url, **kwargs)
            report = run_task(f"Make {target} subtitles for this Hindi recording.", [source], output,
                              steps, Decimal(budget), lambda: ChatPlanner(client))
        return output, report, transcribed_frames, translation_inputs, reviews

    def test_long_audio_exports_verified_english_cues_with_measured_bounds(self):
        output, report, frames, translated, reviews = self.run_subtitle_task(steps=3)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["steps"], 3)
        trace = [json.loads(line) for line in (output / "trace.jsonl").read_text().splitlines()]
        self.assertEqual(trace[-2]["event"], "completion_check")
        self.assertEqual(frames, [8 * SAMPLE_RATE] * 4 + [SAMPLE_RATE // 4])
        self.assertEqual(translated, ["कक्षा कल है।", "सभी छात्र आएं।", "धन्यवाद।"])
        self.assertEqual(reviews, [translated])
        self.assertEqual(report["verification"][-1]["cues"], 4)
        self.assertTrue(all(check["passed"] for check in report["verification"]))
        self.assertIn("audio window boundaries", report["assumptions"][0])
        path, = report["artifacts"]
        self.assertEqual((output / path).read_text(), (
            "1\n00:00:00,000 --> 00:00:08,000\nClass is tomorrow.\n\n"
            "2\n00:00:16,000 --> 00:00:24,000\nAll students should come.\n\n"
            "3\n00:00:24,000 --> 00:00:32,000\nClass is tomorrow.\n\n"
            "4\n00:00:32,000 --> 00:00:32,250\nThank you.\n"
        ))

    def test_requested_source_language_reuses_the_transcript(self):
        output, report, _, translated, reviews = self.run_subtitle_task(target="hi")
        self.assertEqual(report["status"], "completed")
        self.assertEqual(translated, [])
        self.assertEqual(reviews, [])
        self.assertIn("कक्षा कल है।", (output / report["artifacts"][0]).read_text())

    def test_alternate_window_length_preserves_audio_coverage(self):
        _, report, frames, _, _ = self.run_subtitle_task(target="hi", window_seconds=12)
        self.assertEqual(report["status"], "completed")
        self.assertLess(len(frames), 5)
        self.assertLessEqual(max(frames), 12 * SAMPLE_RATE)
        self.assertEqual(sum(frames), 32 * SAMPLE_RATE + SAMPLE_RATE // 4)

    def test_failed_translation_review_prevents_publication(self):
        _, report, _, _, _ = self.run_subtitle_task(approved=False)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["artifacts"], [])
        self.assertFalse(report["verification"][0]["passed"])

    def test_unsupported_subtitle_language_stops_without_provider_calls(self):
        _, report, frames, translated, _ = self.run_subtitle_task(target="fr")
        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(frames, [])
        self.assertEqual(translated, [])

    def test_interrupted_transcription_preserves_completed_windows_as_partial_markdown(self):
        for expected_status, options, requests_sent in (
            ("budget_exhausted", {"budget": "0.03"}, 2),
            ("rate_limited", {"throttle_window": 3}, 3),
        ):
            with self.subTest(status=expected_status):
                output, report, frames, translated, _ = self.run_subtitle_task(**options)
                self.assertEqual(report["status"], expected_status)
                self.assertIn("Window 16–24 seconds", report["explanation"])
                self.assertEqual(report["steps"], 2)
                self.assertEqual(len(frames), requests_sent)
                self.assertEqual(translated, [])
                self.assertIsNone(report["deliverables"][0]["artifact"])
                partial, = report["partial_artifacts"]
                self.assertEqual(report["artifacts"], [partial])
                self.assertTrue(partial.endswith(".md"))
                text = (output / partial).read_text(encoding="utf-8")
                self.assertIn("# Partial transcript", text)
                self.assertIn("Completed 2 windows through 16.000 seconds.", text)
                self.assertIn("## 0.000–8.000 seconds", text)
                self.assertIn("कक्षा कल है।", text)
                self.assertIn("## 8.000–16.000 seconds", text)
                self.assertIn("(No speech returned.)", text)
                self.assertNotIn("## 16.000", text)
                trace = [json.loads(line) for line in (output / "trace.jsonl").read_text().splitlines()]
                event, = (entry for entry in trace if entry["event"] == "partial_artifact")
                self.assertEqual(event["completed_through_seconds"], 16)
                self.assertEqual(event["completed_windows"], 2)
                self.assertEqual(event["source"], "input-1")

    def test_transcription_interruption_before_first_window_creates_no_artifact(self):
        _, report, frames, translated, _ = self.run_subtitle_task(throttle_window=1)
        self.assertEqual(report["status"], "rate_limited")
        self.assertEqual(len(frames), 1)
        self.assertEqual(translated, [])
        self.assertEqual(report["partial_artifacts"], [])
        self.assertEqual(report["artifacts"], [])

    def test_budget_exhaustion_prevents_partial_subtitles_from_being_published(self):
        _, report, frames, translated, _ = self.run_subtitle_task(budget="0.08")
        self.assertEqual(report["status"], "budget_exhausted")
        self.assertEqual(len(frames), 5)
        self.assertEqual(translated, [])
        self.assertEqual(report["artifacts"], [])
