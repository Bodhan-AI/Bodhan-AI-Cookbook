"""Exercise the CLI summary flow with mocked providers and real MP3 encoding."""

from decimal import Decimal
import io
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

import httpx

from bodhan_agent.__main__ import main
from bodhan_mcp.transcription import transcribe_audio


SOURCE = "Class starts at nine on Monday. Students should bring their notebook."
SUMMARY = "Bring your notebook for class at nine on Monday."
TAMIL_SUMMARY = "திங்கட்கிழமை ஒன்பது மணிக்கு வகுப்பிற்கு உங்கள் குறிப்பேட்டைக் கொண்டு வாருங்கள்."


def narration_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 24_000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x03\x00" * 48_000)
    return output.getvalue()


class AudioSummaryTests(unittest.TestCase):
    def invoke(self, *, target: int, approve_summary: bool = True):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source = root / "chapter.txt"
        source.write_text(SOURCE, encoding="utf-8")
        output = root / "run"
        actions = [
            {"action": "declare_deliverables", "deliverables": [
                {"description": "Tamil narration", "format": "mp3", "language": "ta",
                 "duration_seconds": target},
                {"description": "Tamil summary", "format": "txt", "language": "ta"},
            ]},
            {"action": "read", "source": "input-1"},
            {"action": "summarize", "source": "text-1", "target_words": 15},
        ]
        if approve_summary:
            actions.extend([
                {"action": "translate", "source": "text-2", "language": "ta"},
                {"action": "speak", "source": "text-3", "language": "ta", "voice": "Anitha",
                 "deliverable": "deliverable-1"},
                {"action": "save_text", "source": "text-3", "deliverable": "deliverable-2"},
                {"action": "finish", "status": "completed"},
            ])
        else:
            actions.append({"action": "finish", "status": "failed"})
        decisions = iter(actions)
        observed = []

        def answer(value):
            return httpx.Response(200, json={"choices": [
                {"message": {"content": json.dumps(value)}, "finish_reason": "stop"},
            ]})

        def respond(_client, url, **kwargs):
            if url.endswith("/audio/transcriptions"):
                self.assertEqual(kwargs["data"]["language"], "ta")
                observed.append("asr")
                return httpx.Response(200, json={"text": TAMIL_SUMMARY})
            payload = kwargs["json"]
            if url.startswith("https://planner.example/"):
                system, content = (item["content"] for item in payload["messages"])
                state = json.loads(content)
                if system.startswith("Write a factual English summary"):
                    self.assertEqual(state["source"], SOURCE)
                    self.assertEqual(state["target_words"], 15)
                    return answer({"summary": SUMMARY})
                if system.startswith("Review the English summary"):
                    self.assertEqual(state["summary"], SUMMARY)
                    return answer({"approved": approve_summary, "explanation": "Grounding verdict."})
                if system.startswith("Check whether"):
                    return answer({"approved": True, "explanation": "Meaning preserved."})
                return answer(dict(next(decisions), reason="Advance the task."))
            if url.endswith("/audio/speech"):
                self.assertEqual(payload["input"], TAMIL_SUMMARY)
                observed.append("tts")
                return httpx.Response(200, content=narration_wav())
            self.assertEqual(payload["model"], "indic-translate")
            self.assertIn(SUMMARY, json.dumps(payload))
            observed.append("translate")
            response = {"choices": [
                {"message": {"content": TAMIL_SUMMARY}, "finish_reason": "stop"},
            ]}
            return httpx.Response(200, json={**response, "usage": {"prompt_tokens": 8, "completion_tokens": 20, "total_tokens": 28}})

        with (
            patch.dict("os.environ", {
                "LLM_BASE_URL": "https://planner.example/v1", "LLM_MODEL": "test",
                "LLM_API_KEY": "test", "BODHAN_TRANSLATE_KEY": "test",
                "BODHAN_SPEAK_KEY": "test", "BODHAN_TRANSCRIBE_KEY": "test",
            }),
            patch.object(httpx.Client, "post", autospec=True, side_effect=respond),
            patch("bodhan_agent.run.transcribe_audio", wraps=transcribe_audio) as readback,
            patch("sys.argv", ["bodhan_agent", "--task", f"Make a {target}-second Tamil MP3 summary and text.",
                               "--files", str(source), "--out", str(output)]),
            patch("builtins.print"),
        ):
            code = main()

        report = json.loads((output / "report.json").read_text())
        if approve_summary:
            self.assertEqual(observed, ["translate", "tts", "asr"])
            self.assertEqual(Path(readback.call_args.args[0]).suffix, ".mp3")
            self.assertGreater(Decimal(report["cost_inr"]), 0)
        else:
            self.assertEqual(observed, [])
            readback.assert_not_called()
        return code, output, report

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for media verification")
    def test_summary_text_and_timed_mp3_are_verified(self):
        code, output, report = self.invoke(target=2)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "completed")
        audio, text = (output / item["artifact"] for item in report["deliverables"])
        self.assertEqual(audio.suffix, ".mp3")
        self.assertNotEqual(audio.read_bytes()[:4], b"RIFF")
        self.assertEqual(text.read_text(), TAMIL_SUMMARY)
        self.assertTrue(all(check["passed"] for check in report["verification"]))
        self.assertIn("AI-generated audio", (output / "result.md").read_text())

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for media verification")
    def test_duration_miss_leaves_requested_audio_unfulfilled(self):
        code, output, report = self.invoke(target=10)
        self.assertEqual(code, 1)
        duration_check = next(check for check in report["verification"] if check["kind"] == "speech_duration")
        self.assertFalse(duration_check["passed"])
        self.assertIsNone(report["deliverables"][0]["artifact"])
        partial, = report["partial_artifacts"]
        self.assertTrue((output / partial).is_file())

    def test_unapproved_summary_is_never_translated_or_published(self):
        code, _, report = self.invoke(target=2, approve_summary=False)
        self.assertEqual(code, 1)
        self.assertFalse(report["verification"][0]["passed"])
        self.assertEqual(report["artifacts"], [])
