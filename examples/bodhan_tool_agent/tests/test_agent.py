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

    def invoke(self, actions, *, budget="1", pages=1, steps=8, readback_status=200, review_status=200, throttle_finish=False):
        if actions[0]["action"] != "declare_deliverables":
            has_speech = any(item["action"] == "speak" for item in actions)
            deliverable = {"description": "Requested result", "format": "wav" if has_speech else "md"}
            actions = [{"action": "declare_deliverables", "deliverables": [deliverable]}, *actions]
        decisions = iter([
            {**item, "deliverable": item.get("deliverable", "deliverable-1")}
            if item["action"] in {"speak", "save_text"} else item
            for item in actions
        ])

        def planner_reply(request):
            nonlocal throttle_finish
            messages = json.loads(request.content)["messages"]
            state = json.loads(messages[1]["content"])
            if throttle_finish and any(d.get("artifact") for d in state.get("deliverables", [])):
                throttle_finish = False
                return httpx.Response(429, headers={"Retry-After": "2"})
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
        speech = next(event for event in trace if event["event"] == "tool" and event["step"] == 4)
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
        self.assertEqual(coverage["step"], 3)
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
        path, = report["partial_artifacts"]
        self.assertEqual((self.root / "run" / path).read_text(), "ನಾಳೆ ತರಗತಿ ಪ್ರಾರಂಭವಾಗುತ್ತದೆ.")
        self.assertIsNone(report["deliverables"][0]["artifact"])

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
        tool = next(item for item in trace if item["event"] == "tool")
        self.assertEqual(tool["provider_requests"][0]["model"], "indic-ocr")
        self.assertEqual(len(report["partial_artifacts"]), 1)
        partial = self.root / "run" / report["partial_artifacts"][0]
        self.assertEqual(partial.read_text(), "## Page 1\n\nClass starts tomorrow.")
        self.assertIsNone(report["deliverables"][0]["artifact"])

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

    def test_rate_limit_after_save_retries_only_the_planner(self):
        with patch("bodhan_agent.planner.sleep") as wait:
            report, calls = self.invoke([
                {"action": "ocr", "source": "input-1"},
                {"action": "save_text", "source": "text-1"},
                {"action": "finish", "status": "completed"},
            ], throttle_finish=True)
        wait.assert_called_once_with(2)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(self.reads, ["page-1.png"])
        self.assertEqual(calls, 0)
        self.assertEqual(Decimal(report["cost_inr"]), Decimal("0.20"))

    def test_step_limit_stops_before_another_decision(self):
        report, _ = self.invoke([{"action": "ocr", "source": "input-1"}], steps=2)
        self.assertEqual(report["status"], "step_limit")
        self.assertEqual(report["steps"], 2)

    def test_missing_second_deliverable_cannot_claim_completion(self):
        report, calls = self.invoke([
            {"action": "declare_deliverables", "assumptions": ["Use Markdown for the document."],
             "deliverables": [
                {"description": "Extracted Markdown", "format": "md"},
                {"description": "Kannada audio", "format": "wav", "language": "kn"},
            ]},
            {"action": "ocr", "source": "input-1"},
            {"action": "save_text", "source": "text-1", "deliverable": "deliverable-1"},
            {"action": "finish", "status": "completed"},
        ])
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["assumptions"], ["Use Markdown for the document."])
        self.assertEqual(calls, 0)
        text, audio = report["deliverables"]
        self.assertTrue((self.root / "run" / text["artifact"]).is_file())
        self.assertIsNone(audio["artifact"])

    def test_planner_corrects_schema_error_with_current_state(self):
        inputs = []

        def reply(request):
            data = json.loads(request.content)
            inputs.append(json.loads(data["messages"][1]["content"]))
            action = {"action": "save_text", "source": "text-2",
                      "deliverable": "deliverable-1", "reason": "Save the document."}
            if len(inputs) == 1:
                action["format"] = "md"
            return httpx.Response(200, json={"choices": [{"message": {
                "content": json.dumps(action),
            }}]})

        state = {"deliverables": [{"id": "deliverable-1", "format": "md"}]}
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            action = ChatPlanner(client).decide(state)
        self.assertEqual(action.action, "save_text")
        self.assertEqual(inputs[1]["deliverables"], state["deliverables"])
        self.assertEqual(inputs[1]["action_correction"]["error_types"], ["extra_forbidden"])

    def test_planner_cannot_redeclare_after_deliverables_are_set(self):
        replies = iter([
            {"action": "declare_deliverables", "deliverables": [
                {"description": "New output", "format": "txt"},
            ], "reason": "Change the output."},
            {"action": "ocr", "source": "input-1", "reason": "Read the PDF."},
        ])

        def reply(_request):
            return httpx.Response(200, json={"choices": [{"message": {
                "content": json.dumps(next(replies)),
            }}]})

        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            result = ChatPlanner(client).decide({
                "deliverables": [{"id": "deliverable-1", "format": "md"}],
                "inputs": [{"id": "input-1", "name": "handout.pdf"}],
            })
        self.assertEqual(result.action, "ocr")

    def test_unsupported_declared_format_stops_before_bodhan(self):
        report, calls = self.invoke([
            {"action": "declare_deliverables", "deliverables": [
                {"description": "Requested video", "format": "other"},
            ]},
        ])
        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(calls, 0)
        self.assertEqual(self.reads, [])
        self.assertEqual(report["cost_inr"], "0")

    def test_document_workflow_publishes_source_and_verified_translation(self):
        report, _ = self.invoke([
            {"action": "declare_deliverables", "deliverables": [
                {"description": "Source Markdown", "format": "md"},
                {"description": "Kannada Markdown", "format": "md", "language": "kn"},
            ]},
            {"action": "ocr", "source": "input-1"},
            {"action": "save_text", "source": "text-1", "deliverable": "deliverable-1"},
            {"action": "translate", "source": "text-1", "language": "kn"},
            {"action": "save_text", "source": "text-2", "deliverable": "deliverable-2"},
            {"action": "finish", "status": "completed"},
        ])
        self.assertEqual(report["status"], "completed")
        source, translation = report["deliverables"]
        root = self.root / "run"
        self.assertEqual((root / source["artifact"]).read_text(), "Class starts tomorrow.")
        self.assertEqual((root / translation["artifact"]).read_text(), "ನಾಳೆ ತರಗತಿ ಪ್ರಾರಂಭವಾಗುತ್ತದೆ.")
        self.assertTrue(report["verification"][0]["passed"])
