"""Exercise the CLI summary flow with mocked providers and real MP3 encoding."""

import io
import json
import shutil
import unittest
import wave
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import httpx
from bodhan_agent.__main__ import main
from bodhan_agent.planner import ACTIONS
from bodhan_agent.run import run_task
from bodhan_mcp.transcription import TranscriptionResult, read_audio

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
    def invoke(self, *, target: int, approve_summary: bool = True, steps: int = 8):
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
                {"description": "Tamil summary", "format": "txt", "language": "ta",
                 "narration_for": "deliverable-1"},
            ]},
            {"action": "read", "source": "input-1"},
            {"action": "summarize", "source": "text-1", "target_words": 15},
        ]
        if approve_summary:
            actions.extend([
                {"action": "translate", "source": "text-2", "language": "ta"},
                {"action": "save_text", "source": "text-3", "deliverable": "deliverable-2"},
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
                    self.assertEqual(state["target_words"], 10 if target == 2 else 15)
                    return answer({"summary": SUMMARY})
                if system.startswith("Check that an English summary"):
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
            patch("bodhan_agent.run.read_audio", wraps=read_audio) as readback,
            patch("sys.argv", ["bodhan_agent", "--task", f"Make a {target}-second Tamil MP3 summary and text.",
                               "--files", str(source), "--out", str(output), "--max-steps", str(steps)]),
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
    def test_summary_clamps_word_target_and_verifies_timed_mp3(self):
        code, output, report = self.invoke(target=2, steps=7)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["steps"], 7)
        audio, text = (output / item["artifact"] for item in report["deliverables"])
        self.assertEqual(audio.suffix, ".mp3")
        self.assertNotEqual(audio.read_bytes()[:4], b"RIFF")
        self.assertEqual(text.read_text(), TAMIL_SUMMARY)
        self.assertTrue(all(check["passed"] for check in report["verification"]))
        self.assertIn("AI-generated audio", (output / "result.md").read_text())

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for media verification")
    def test_step_limit_leaves_verified_audio_with_pending_script(self):
        code, _, report = self.invoke(target=2, steps=6)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "step_limit")
        audio, script = report["deliverables"]
        self.assertTrue(audio["artifact"].endswith(".mp3"))
        self.assertIsNone(script["artifact"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for media verification")
    def test_duration_miss_leaves_requested_audio_unfulfilled(self):
        code, output, report = self.invoke(target=10)
        self.assertEqual(code, 1)
        duration_check = next(check for check in report["verification"] if check["kind"] == "speech_duration")
        self.assertFalse(duration_check["passed"])
        self.assertIsNone(report["deliverables"][0]["artifact"])
        self.assertIsNone(report["deliverables"][1]["artifact"])
        partial, = report["partial_artifacts"]
        self.assertTrue((output / partial).is_file())

    def test_unapproved_summary_is_never_translated_or_published(self):
        code, _, report = self.invoke(target=2, approve_summary=False)
        self.assertEqual(code, 1)
        self.assertFalse(report["verification"][0]["passed"])
        self.assertEqual(report["artifacts"], [])

    def test_only_revised_verified_narration_can_be_saved(self):
        original, revised = "Read this long version of the notice.", "Read this shorter notice."
        actions = [
            {"action": "declare_deliverables", "deliverables": [
                {"description": "Matching English script", "format": "md", "language": "en"},
                {"description": "English recap", "format": "mp3", "language": "en", "duration_seconds": 40},
            ]},
            {"action": "read", "source": "input-1"},
            {"action": "read", "source": "input-2"},
            {"action": "save_text", "source": "text-1", "deliverable": "deliverable-1"},
            {"action": "speak", "source": "text-1", "language": "en", "voice": "Arun", "deliverable": "deliverable-2"},
            {"action": "speak", "source": "text-2", "language": "en", "voice": "Arun", "deliverable": "deliverable-2"},
            {"action": "save_text", "source": "text-1", "deliverable": "deliverable-1"},
            {"action": "save_text", "source": "text-2", "deliverable": "deliverable-1"},
            {"action": "finish", "status": "completed"},
        ]
        planner = Mock()
        planner.decide.side_effect = [ACTIONS.validate_python({**action, "reason": "Advance the task."})
                                      for action in actions]

        def speak(text, language, voice, path, *, target_seconds):
            Path(path).write_bytes(b"mock audio")
            return Mock(audio_path=path)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = [root / "original.txt", root / "revised.txt"]
            inputs[0].write_text(original)
            inputs[1].write_text(revised)
            output = root / "run"
            with patch("bodhan_agent.run.synthesize_speech", side_effect=speak), patch(
                "bodhan_agent.run.read_audio", side_effect=[
                    TranscriptionResult(text=original, duration_seconds=50, windows=[]),
                    TranscriptionResult(text=revised, duration_seconds=40, windows=[]),
                ],
            ):
                report = run_task("Save a 40-second MP3 and matching script.", inputs, output,
                                  len(actions), Decimal("1"), lambda: planner)
            self.assertEqual(report["status"], "completed")
            script, audio = (item["artifact"] for item in report["deliverables"])
            self.assertEqual((output / script).read_text(), revised)
            self.assertEqual(report["partial_artifacts"], ["artifacts/deliverable-2.mp3"])
            pair = next(check for check in report["verification"] if check["kind"] == "narration_pair")
            self.assertTrue(pair["passed"])
            self.assertEqual((pair["source"], pair["script_artifact"], pair["audio_artifact"]),
                             ("text-2", script, audio))
            trace = [json.loads(line) for line in (output / "trace.jsonl").read_text().splitlines()]
            observations = {event["step"]: event["observation"] for event in trace if event["event"] == "tool"}
            self.assertIn("before saving its script", observations[4]["error"])
            self.assertIn("Save narration text-2", observations[7]["error"])

    def test_same_language_pair_defaults_with_an_explicit_opt_out(self):
        for link, expected in ((None, "deliverable-2"), ("independent", None)):
            with self.subTest(link=link):
                declaration = ACTIONS.validate_python({
                    "action": "declare_deliverables", "reason": "Declare the text and audio recap.",
                    "deliverables": [
                        {"description": "Text output", "format": "txt", "language": "en", "narration_for": link},
                        {"description": "Spoken recap", "format": "mp3", "language": "en"},
                    ],
                })
                self.assertEqual(declaration.deliverables[0].linked_audio, expected)
