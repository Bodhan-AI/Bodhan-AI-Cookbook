"""
Unit tests for Bodhan Autonomous Task Agent and Reporter.
Tests budget enforcement, impossible task detection, and output layout generation.
"""

import json
import tempfile
import unittest
from pathlib import Path

from bodhan_agent.agent import TaskAgent
from bodhan_agent.reporter import AgentReporter


class TestTaskAgent(unittest.TestCase):

    def test_reporter_layout_generation(self):
        """Verify the reporter writes result.md, report.json, trace.jsonl, and artifacts/."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            reporter = AgentReporter(out_dir=tmp_dir)

            # Record a mock step
            reporter.record_step(
                step_idx=1,
                plan="Inspect audio duration",
                tool="inspect_media",
                arguments={"file_path": "audio.wav"},
                result_summary="Duration 12.0s",
                latency_sec=0.05,
                cost_inr=0.0
            )

            # Save a mock artifact
            art_file = Path(tmp_dir) / "sample.txt"
            art_file.write_text("Hello artifact")
            reporter.add_artifact(art_file, description="Test artifact")

            # Record verification
            reporter.add_verification(
                check_name="Quality Check",
                target_artifact="sample.txt",
                passed=True,
                metric="Accuracy",
                score=1.0,
                details="Perfect match"
            )

            # Write results
            res_md = reporter.write_result_md(
                task="Test task",
                status="completed",
                summary="Did step 1",
                final_answer="Answer is 42",
                assumptions=["Assumption 1"]
            )
            rep_json = reporter.write_report_json(status="completed", task="Test task")

            # Verify files exist
            self.assertTrue(res_md.exists())
            self.assertTrue(rep_json.exists())
            self.assertTrue(reporter.trace_file.exists())
            self.assertTrue(reporter.artifacts_dir.exists())

            # Verify trace.jsonl contents
            with open(reporter.trace_file, "r") as f:
                lines = f.readlines()
                self.assertEqual(len(lines), 1)
                entry = json.loads(lines[0])
                self.assertEqual(entry["step"], 1)
                self.assertEqual(entry["tool"], "inspect_media")

            # Verify report.json contents
            with open(rep_json, "r") as f:
                data = json.load(f)
                self.assertEqual(data["status"], "completed")
                self.assertEqual(data["steps"], 1)
                self.assertEqual(len(data["verification_results"]), 1)

    def test_impossible_french_task_honest_failure(self):
        """Verify the agent immediately detects French as unsupported and halts without hallucinating."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = TaskAgent(
                task="Translate this video into French",
                out_dir=tmp_dir,
            )

            result = agent.run()
            self.assertEqual(result["status"], "impossible")
            self.assertIn("French", result["reason"])

            # Verify report.json and result.md were produced
            report_file = Path(tmp_dir) / "report.json"
            result_file = Path(tmp_dir) / "result.md"
            self.assertTrue(report_file.exists())
            self.assertTrue(result_file.exists())

            with open(report_file, "r") as f:
                report_data = json.load(f)
                self.assertEqual(report_data["status"], "impossible")

            with open(result_file, "r") as f:
                md_text = f.read()
                self.assertIn("HONEST FAILURE", md_text)
                self.assertIn("French", md_text)

    def test_missing_files_path_honest_failure(self):
        """Verify the agent immediately detects non-existent input files path and halts honestly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = TaskAgent(
                task="Make a two-minute Tamil audio summary of this chapter",
                files_dir="/non/existent/path/to/inputs",
                out_dir=tmp_dir,
            )
            result = agent.run()
            self.assertEqual(result["status"], "impossible")
            self.assertIn("does not exist on disk", result["reason"])

    def test_zero_budget_honest_failure(self):
        """Verify the agent halts honestly when budget is ₹0.00."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = TaskAgent(
                task="Translate this text to Hindi",
                out_dir=tmp_dir,
                max_cost_inr=0.0,
            )
            result = agent.run()
            self.assertEqual(result["status"], "impossible")
            self.assertIn("insufficient", result["reason"])

    def test_indic_characters_detection(self):
        """Verify detection of Indian language Unicode scripts."""
        from bodhan_agent.agent import contains_indic_characters
        self.assertTrue(contains_indic_characters("காலை வணக்கம்"))
        self.assertTrue(contains_indic_characters("नमस्ते दुनिया"))
        self.assertTrue(contains_indic_characters("শুভ সকাল"))
        self.assertTrue(contains_indic_characters("సుప్రభాతం"))
        self.assertFalse(contains_indic_characters("Good morning world"))
        self.assertFalse(contains_indic_characters("12345 !@#$%"))

    def test_translation_verification_logic(self):
        """Verify the translation verification correctly validates Indic scripts."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = TaskAgent(task="Test task", out_dir=tmp_dir)
            agent._verify_translation("Good morning", "காலை வணக்கம்", "ta")
            self.assertEqual(len(agent.reporter.verifications), 1)
            v = agent.reporter.verifications[0]
            self.assertTrue(v["passed"])
            self.assertEqual(v["check"], "Translation Coverage & Script Check")

    def test_speech_back_transcription_verification(self):
        """Verify strict similarity threshold in speech back-transcription."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent = TaskAgent(task="Test task", out_dir=tmp_dir)
            # Create a mock wav file
            wav_file = Path(tmp_dir) / "test.wav"
            wav_file.write_bytes(b"RIFF" + b"\x00" * 100)
            
            # Mock speech_to_text_tool to return different text (low similarity)
            import bodhan_agent.agent as agent_module
            orig_tool = agent_module.speech_to_text_tool
            try:
                agent_module.speech_to_text_tool = lambda audio_path, language: {"text": "something completely wrong"}
                agent._verify_synthesized_speech(str(wav_file), "original target text", "en")
                v = agent.reporter.verifications[0]
                self.assertFalse(v["passed"])
            finally:
                agent_module.speech_to_text_tool = orig_tool


if __name__ == "__main__":
    unittest.main()
