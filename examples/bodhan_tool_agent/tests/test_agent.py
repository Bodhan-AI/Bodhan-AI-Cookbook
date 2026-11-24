"""Tests for the Task Agent execution loop, impossible tasks, and verification."""

import os
os.environ["BODHAN_MOCK"] = "1"

import tempfile
import unittest
from pathlib import Path
from bodhan_agent.agent import TaskAgent


class TestAgent(unittest.TestCase):
    def test_impossible_task_french(self):
        with tempfile.TemporaryDirectory() as td:
            sample = Path(td) / "test.wav"
            sample.write_bytes(b"RIFFdummydata")

            out_dir = Path(td) / "out_impossible"
            agent = TaskAgent(
                task="Translate this video into French",
                files_path=str(sample),
                out_dir=str(out_dir),
            )
            result = agent.run()
            self.assertEqual(result["status"], "impossible_task")
            self.assertTrue((out_dir / "result.md").exists())
            self.assertTrue((out_dir / "report.json").exists())
            self.assertIn("French", (out_dir / "result.md").read_text(encoding="utf-8"))

    def test_agent_subtitles_task(self):
        sample_wav = Path(__file__).resolve().parent.parent / "sample_data" / "sample_hindi.wav"
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out_subtitles"
            agent = TaskAgent(
                task="Transcribe this Hindi voice note and give me English subtitles",
                files_path=str(sample_wav),
                out_dir=str(out_dir),
            )
            result = agent.run()
            self.assertEqual(result["status"], "completed")
            self.assertTrue((out_dir / "result.md").exists())
            self.assertTrue((out_dir / "report.json").exists())
            self.assertTrue((out_dir / "trace.jsonl").exists())
            self.assertTrue((out_dir / "artifacts" / "subtitles_en.srt").exists())
            self.assertTrue((out_dir / "artifacts" / "transcript.txt").exists())

    def test_tool_call_deduplication(self):
        sample_wav = Path(__file__).resolve().parent.parent / "sample_data" / "sample_hindi.wav"
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out_dedup"
            agent = TaskAgent(
                task="Transcribe audio",
                files_path=str(sample_wav),
                out_dir=str(out_dir),
            )
            # Call 1: Initial execution
            res1 = agent.execute_tool(
                "bodhan_inspect_media",
                {"file_path": str(sample_wav)},
                plan_reason="Initial media inspection",
            )
            self.assertEqual(agent.budget.current_step, 1)
            self.assertEqual(agent._dedup_count, 0)
            initial_cost = agent.budget.total_cost_inr

            # Call 2: Identical execution (hits de-duplication cache)
            res2 = agent.execute_tool(
                "bodhan_inspect_media",
                {"file_path": str(sample_wav)},
                plan_reason="Redundant media inspection",
            )
            self.assertEqual(agent.budget.current_step, 2)
            self.assertEqual(agent._dedup_count, 1)
            self.assertEqual(agent.budget.total_cost_inr, initial_cost)
            self.assertEqual(res1.duration_seconds, res2.duration_seconds)

            # Check paid tool de-duplication (bodhan_translate)
            res_trans1 = agent.execute_tool(
                "bodhan_translate",
                {"text": "नमस्ते दुनिया", "target_language": "en"},
                plan_reason="First translation",
            )
            cost_after_first = agent.budget.total_cost_inr
            self.assertGreater(cost_after_first, initial_cost)

            res_trans2 = agent.execute_tool(
                "bodhan_translate",
                {"text": "नमस्ते दुनिया", "target_language": "en"},
                plan_reason="Duplicate translation",
            )
            self.assertEqual(agent._dedup_count, 2)
            # Cost must NOT increase on duplicate call
            self.assertEqual(agent.budget.total_cost_inr, cost_after_first)
            self.assertEqual(res_trans1.translation, res_trans2.translation)

    def test_path_traversal_blocked_and_traced(self):
        sample_wav = Path(__file__).resolve().parent.parent / "sample_data" / "sample_hindi.wav"
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "nested" / "out_sec"
            agent = TaskAgent(
                task="Save artifact",
                files_path=str(sample_wav),
                out_dir=str(out_dir),
            )
            with self.assertRaises(ValueError):
                agent.execute_tool(
                    "bodhan_save_artifact",
                    {"filename": "../../escaped.txt", "content": "hacked"},
                    plan_reason="Attempt path traversal",
                )
            self.assertFalse((Path(td) / "escaped.txt").exists())
            # Failed tool call MUST be counted in budget and logged to trace.jsonl
            self.assertEqual(agent.budget.current_step, 1)
            trace_text = (out_dir / "trace.jsonl").read_text(encoding="utf-8")
            self.assertIn("[ERROR]", trace_text)
            self.assertIn("path traversal", trace_text)

    def test_impossible_task_arabic(self):
        with tempfile.TemporaryDirectory() as td:
            sample = Path(td) / "test.wav"
            sample.write_bytes(b"RIFFdummydata")
            out_dir = Path(td) / "out_arabic"
            agent = TaskAgent(
                task="Transcribe this audio and translate into Arabic",
                files_path=str(sample),
                out_dir=str(out_dir),
            )
            result = agent.run()
            self.assertEqual(result["status"], "impossible_task")
            self.assertEqual(agent.budget.total_cost_inr, 0.0)

    def test_ocr_block_accepts_float_bbox(self):
        from bodhan_mcp.models import OCRBlock
        blk = OCRBlock(order=0, label="text", bbox_xyxy=[9.1, 17.4, 622.1, 113.5], conf=0.98, text="Hello")
        self.assertEqual(blk.bbox_xyxy[0], 9.1)

    def test_verifier_rejects_wrong_script(self):
        from bodhan_agent.verifier import Verifier
        v = Verifier()
        ok, msg = v.verify_translation("नमस्ते", "नमस्ते", "ta", script="native")
        self.assertFalse(ok)
        self.assertIn("script mismatch", msg.lower())
        ok_ta, _ = v.verify_translation("नमस्ते", "வணக்கம்", "ta", script="native")
        self.assertTrue(ok_ta)

    def test_ground_rule1_indic_provenance_guard(self):
        sample_wav = Path(__file__).resolve().parent.parent / "sample_data" / "sample_hindi.wav"
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out_prov"
            agent = TaskAgent(
                task="Test provenance",
                files_path=str(sample_wav),
                out_dir=str(out_dir),
            )
            # 1. Unverified Indic text in bodhan_save_artifact MUST be rejected
            with self.assertRaises(ValueError) as ctx:
                agent.execute_tool(
                    "bodhan_save_artifact",
                    {"filename": "llm_invented.txt", "content": "यह एलएलएम द्वारा लिखा गया है"},
                    plan_reason="LLM tries to save invented Hindi",
                )
            self.assertIn("Ground Rule 1 violation", str(ctx.exception))

            # 2. Unverified Indic text in bodhan_speak MUST be rejected
            with self.assertRaises(ValueError) as ctx2:
                agent.execute_tool(
                    "bodhan_speak",
                    {"text": "வணக்கம் உலகம்", "language": "ta"},
                    plan_reason="LLM tries to speak invented Tamil",
                )
            self.assertIn("Ground Rule 1 violation", str(ctx2.exception))

            # 3. Indic text produced by bodhan_translate MUST be allowed
            tr = agent.execute_tool(
                "bodhan_translate",
                {"text": "Welcome to the science fair", "target_language": "hi"},
                plan_reason="Translate via Bodhan tool",
            )
            saved = agent.execute_tool(
                "bodhan_save_artifact",
                {"filename": "legit_hi.txt", "content": tr.translation},
                plan_reason="Save Bodhan-produced Hindi",
            )
            self.assertEqual(saved["status"], "saved")


if __name__ == "__main__":
    unittest.main()
