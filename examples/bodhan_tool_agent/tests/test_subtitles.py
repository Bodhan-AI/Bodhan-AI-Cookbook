import io
import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

import httpx

from bodhan_agent.planner import ChatPlanner
from bodhan_agent.run import run_task
from bodhan_mcp.transcription import SAMPLE_RATE


class SubtitleTests(unittest.TestCase):
    def run_subtitle_task(self, *, target="en", approved=True, budget="1"):
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
            {"action": "transcribe", "source": "input-1", "language": "hi"},
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
                              4, Decimal(budget), lambda: ChatPlanner(client))
        return output, report, transcribed_frames, translation_inputs, reviews

    def test_long_audio_exports_verified_english_cues_with_measured_bounds(self):
        output, report, frames, translated, reviews = self.run_subtitle_task()
        self.assertEqual(report["status"], "completed")
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

    def test_budget_exhaustion_prevents_partial_subtitles_from_being_published(self):
        _, report, frames, translated, _ = self.run_subtitle_task(budget="0.08")
        self.assertEqual(report["status"], "budget_exhausted")
        self.assertEqual(len(frames), 5)
        self.assertEqual(translated, [])
        self.assertEqual(report["artifacts"], [])
