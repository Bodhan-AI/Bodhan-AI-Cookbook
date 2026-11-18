import io
import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave

import httpx

from bodhan_agent.planner import ACTIONS, ChatPlanner
from bodhan_agent.run import run_task


def wav_bytes(rate=24000):
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\x02" * (rate // 10))
    return data.getvalue()


class AgentTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pdf = self.root / "notice.pdf"
        self.pdf.touch()
        self.reads = []
        self.env = patch.dict("os.environ", {
            "LLM_BASE_URL": "https://planner.example/v1",
            "LLM_MODEL": "test-planner",
            "LLM_API_KEY": "test-key",
            "BODHAN_OCR_KEY": "test-key",
            "BODHAN_TRANSLATE_KEY": "test-key",
            "BODHAN_SPEAK_KEY": "test-key",
            "BODHAN_TRANSCRIBE_KEY": "test-key",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def trace(self):
        return [json.loads(line) for line in (self.root / "run" / "trace.jsonl").read_text().splitlines()]

    def invoke(self, actions, *, budget="1", pages=1, steps=8, readback_status=200, review_status=200):
        decisions = iter(actions)

        def planner_reply(request):
            messages = json.loads(request.content)["messages"]
            if "Check whether" in messages[0]["content"]:
                if review_status != 200:
                    return httpx.Response(review_status)
                answer = {"approved": True, "explanation": "Meaning preserved."}
            else:
                answer = dict(next(decisions), reason="Advance the requested task.")
                ACTIONS.validate_python(answer)
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

        def render(_, directory):
            images = []
            for number in range(1, pages + 1):
                image = directory / f"page-{number}.png"
                image.write_bytes(bytes([number]))
                images.append(image)
            return images

        def bodhan_reply(_client, url, **kwargs):
            if url.endswith("/audio/transcriptions"):
                self.assertEqual(kwargs["data"]["language"], "kn")
                return httpx.Response(readback_status, json={"text": "ನಾಳೆ ತರಗತಿ ಪ್ರಾರಂಭವಾಗುತ್ತದೆ"})
            payload = kwargs["json"]
            if payload["model"] == "indic-ocr":
                from base64 import b64decode

                data = payload["messages"][0]["content"][0]["image_url"]["url"]
                number, = b64decode(data.split(",", 1)[1])
                self.reads.append(f"page-{number}.png")
                return httpx.Response(200, json={"choices": [{
                    "finish_reason": "stop", "message": {"content": "Class starts tomorrow."},
                }]})
            if payload["model"] == "indic-speak":
                self.assertEqual(payload["input"], "ನಾಳೆ ತರಗತಿ ಪ್ರಾರಂಭವಾಗುತ್ತದೆ.")
                return httpx.Response(200, content=wav_bytes())
            self.assertEqual(payload["max_output_tokens"], 2048)
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": "ನಾಳೆ ತರಗತಿ ಪ್ರಾರಂಭವಾಗುತ್ತದೆ."}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 20, "total_tokens": 24},
            })

        def normalize(_, destination):
            destination.write_bytes(wav_bytes(16000))

        with httpx.Client(transport=httpx.MockTransport(planner_reply)) as client:
            with (
                patch("bodhan_mcp.ocr._render_pdf", side_effect=render),
                patch.object(httpx.Client, "post", autospec=True, side_effect=bodhan_reply) as post,
                patch("bodhan_mcp.transcription._normalize", side_effect=normalize),
            ):
                # Leave the planner's own HTTP client on its mock transport.
                client.post = lambda url, **kwargs: client.request("POST", url, **kwargs)
                report = run_task("Process this notice.", [self.pdf], self.root / "run", steps,
                                  Decimal(budget), lambda: ChatPlanner(client))
                provider_calls = post.call_count - len(self.reads)
        return report, provider_calls

    def test_pdf_to_verified_audio(self):
        report, calls = self.invoke([
            {"action": "ocr", "source": "input-1"},
            {"action": "translate", "source": "text-1", "language": "kn"},
            {"action": "speak", "source": "text-2", "language": "kn", "voice": "Deepika"},
            {"action": "finish", "status": "completed"},
        ])
        self.assertEqual(report["status"], "completed")
        self.assertEqual(calls, 3)
        self.assertEqual([item["kind"] for item in report["verification"]],
                         ["translation_coverage", "speech_artifact_created", "speech_content_match"])
        artifact = self.root / "run" / report["artifacts"][0]
        with wave.open(str(artifact)) as audio:
            self.assertEqual(audio.getnframes(), 2400)
        self.assertTrue((self.root / "run" / "result.md").is_file())
        self.assertTrue((self.root / "run" / "report.json").is_file())

        trace = self.trace()
        readback = next(event for event in trace if event.get("method") == "back_transcription")
        speech = next(event for event in trace if event["event"] == "tool" and event["step"] == 3)
        coverage = next(event for event in trace if event.get("method") == "planner_review")
        self.assertEqual(readback["step"], speech["step"])
        self.assertEqual(readback["source"], "text-2")
        self.assertEqual(readback["artifact"], report["artifacts"][0])
        self.assertEqual(readback["language"], "kn")
        self.assertTrue(readback["passed"])
        self.assertEqual([request["model"] for request in readback["provider_requests"]], ["indic-transcribe"])
        self.assertGreater(Decimal(readback["cost_inr"]), 0)
        self.assertGreaterEqual(readback["latency_seconds"], 0)
        self.assertEqual([request["model"] for request in speech["provider_requests"]], ["indic-speak"])
        self.assertEqual(coverage["step"], 2)
        self.assertGreaterEqual(coverage["latency_seconds"], 0)
        self.assertEqual(coverage["provider_requests"], [])
        accounted = sum(Decimal(event["cost_inr"]) for event in trace
                        if event["event"] in {"tool", "verification"})
        self.assertEqual(accounted, Decimal(report["cost_inr"]))

    def test_readback_failure_is_timed_and_does_not_lose_audio(self):
        report, _ = self.invoke([
            {"action": "ocr", "source": "input-1"},
            {"action": "translate", "source": "text-1", "language": "kn"},
            {"action": "speak", "source": "text-2", "language": "kn", "voice": "Deepika"},
            {"action": "finish", "status": "completed"},
        ], readback_status=503)
        self.assertEqual(report["status"], "incomplete")
        self.assertTrue((self.root / "run" / report["artifacts"][0]).is_file())
        readback = next(event for event in self.trace() if event.get("method") == "back_transcription")
        self.assertFalse(readback["passed"])
        self.assertIn("503", readback["explanation"])
        self.assertGreaterEqual(readback["latency_seconds"], 0)
        self.assertEqual([request["model"] for request in readback["provider_requests"]], ["indic-transcribe"])

    def test_unavailable_translation_reviewer_records_failed_check(self):
        report, _ = self.invoke([
            {"action": "ocr", "source": "input-1"},
            {"action": "translate", "source": "text-1", "language": "kn"},
            {"action": "finish", "status": "failed"},
        ], review_status=403)
        review = next(event for event in self.trace() if event.get("method") == "planner_review")
        self.assertEqual(report["status"], "failed")
        self.assertFalse(review["passed"])
        self.assertIn("403", review["explanation"])
        self.assertGreaterEqual(review["latency_seconds"], 0)

    def test_extract_only_skips_translation_and_synthesis(self):
        report, calls = self.invoke([
            {"action": "ocr", "source": "input-1"},
            {"action": "save_text", "source": "text-1"},
            {"action": "finish", "status": "completed"},
        ])
        self.assertEqual(report["status"], "completed")
        self.assertEqual(calls, 0)
        self.assertEqual(Decimal(report["cost_inr"]), Decimal("0.20"))

    def test_budget_can_stop_between_pages_inside_one_tool_call(self):
        report, calls = self.invoke([{"action": "ocr", "source": "input-1"}],
                                    budget="0.30", pages=2)
        self.assertEqual(report["status"], "budget_exhausted")
        self.assertEqual(self.reads, ["page-1.png"])
        self.assertEqual(calls, 0)
        self.assertEqual(Decimal(report["cost_inr"]), Decimal("0.20"))
        trace = self.trace()
        self.assertEqual([item["event"] for item in trace], ["start", "plan", "tool", "summary"])
        self.assertEqual(trace[2]["provider_requests"][0]["model"], "indic-ocr")

    def test_planner_retries_transient_service_unavailable_and_timeout(self):
        attempts = []

        def reply(request):
            attempts.append(request)
            if len(attempts) == 1:
                return httpx.Response(503)
            if len(attempts) == 2:
                raise httpx.ReadTimeout("Test read timeout", request=request)
            return httpx.Response(200, json={"choices": [{"message": {
                "content": json.dumps({"action": "finish", "status": "unsupported",
                                       "reason": "Unsupported task."}),
            }}]})

        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            with patch("bodhan_agent.planner.sleep") as wait:
                action = ChatPlanner(client).decide({"task": "Unsupported task"})
        self.assertEqual(action.status, "unsupported")
        self.assertEqual(len(attempts), 3)
        self.assertEqual([args[0] for args, _ in wait.call_args_list], [1.0, 2.0])

    def test_step_limit_stops_before_another_decision(self):
        report, _ = self.invoke([{"action": "ocr", "source": "input-1"}], steps=1)
        self.assertEqual(report["status"], "step_limit")
        self.assertEqual(report["steps"], 1)
