import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from bodhan_agent.clip_language import assess_transcript
from bodhan_agent.planner import Declare, Deliverable, Finish, TranslateMatchingClips
from bodhan_agent.run import run_task
from bodhan_mcp.api_requests import BodhanRateLimit
from bodhan_mcp.request_budget import BudgetExhausted
from bodhan_mcp.server import TokenUsage, TranslationResult
from bodhan_mcp.transcription import TranscriptionResult


class ClipSelectionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.planner = Mock()
        self.planner.decide.side_effect = [
            Declare(action="declare_deliverables", reason="Declare a combined list.",
                    deliverables=[Deliverable(description="Telugu clips with English translations",
                                              format="md", language="en")]),
            TranslateMatchingClips(action="translate_matching_clips", reason="Select Telugu clips.",
                                   source_language="te", target_language="en", deliverable="deliverable-1"),
            Finish(action="finish", reason="The result is available.", status="completed"),
        ]

    def invoke(self, transcripts):
        inputs = []
        for index in range(len(transcripts)):
            file = self.root / f"clip-{index + 1}.wav"
            file.touch()
            inputs.append(file)
        other = self.root / "note.txt"
        other.touch()
        translation = TranslationResult(translation="Good morning", target_language="en", script="native",
                                        truncated=False, usage=TokenUsage(
                                            prompt_tokens=2, completion_tokens=2, total_tokens=4),
                                        segments=[dict(source_start=0, source_end=len(transcripts[0]),
                                                       output_start=0, output_end=12)])
        self.planner.review.return_value = Mock(approved=True, explanation="Meaning preserved.")
        with patch("bodhan_agent.run.read_audio") as transcribe, patch(
            "bodhan_agent.run.translate_text", return_value=translation,
        ) as translate:
            transcribe.side_effect = [
                TranscriptionResult(text=text, duration_seconds=1, windows=[])
                if isinstance(text, str) else text for text in transcripts
            ]
            report = run_task("Which clips are in Telugu? Translate those into English.",
                              [*inputs, other], self.root / "run", 4, Decimal("1"), lambda: self.planner)
        return report, transcribe, translate

    def test_translates_only_matches_without_language_hint(self):
        report, transcribe, translate = self.invoke([
            "శుభోదయం", "सुप्रभात", "శుభోదయం friend", "hi",
        ])
        self.assertEqual(report["status"], "completed")
        self.assertEqual([call.args[:2] for call in transcribe.call_args_list],
                         [(str(self.root / f"clip-{i}.wav"), None) for i in range(1, 5)])
        translate.assert_called_once_with("శుభోదయం", "en")
        self.planner.review.assert_called_once_with("శుభోదయం", "Good morning", "en")
        clips = report["clip_selections"]["deliverable-1"]["clips"]
        self.assertEqual([clip["status"] for clip in clips], ["match", "non_match", "mixed", "unknown"])
        artifact = self.root / "run" / report["artifacts"][0]
        content = artifact.read_text()
        self.assertIn("Good morning", content)
        self.assertIn("multiple scripts", content)
        self.assertIn("too little letter evidence", content)

    def test_budget_exhaustion_preserves_verified_translation_and_pending_clip(self):
        report, _, translate = self.invoke(["శుభోదయం", BudgetExhausted("Budget exhausted.")])
        self.assertEqual(report["status"], "budget_exhausted")
        self.assertEqual(translate.call_count, 1)
        clips = report["clip_selections"]["deliverable-1"]["clips"]
        self.assertEqual([clip["status"] for clip in clips], ["match", "pending"])
        self.assertIsNone(report["deliverables"][0]["artifact"])
        artifact = self.root / "run" / report["partial_artifacts"][0]
        self.assertIn("Good morning", artifact.read_text())

    def test_shared_script_and_romanization_are_inconclusive(self):
        self.assertEqual(assess_transcript("सुप्रभात", "hi")[0], "unknown")
        self.assertEqual(assess_transcript("subhodayam", "te")[0], "unknown")
        self.assertEqual(assess_transcript("", "te")[0], "unknown")

    def test_rate_limit_preserves_verified_translation_and_stops_scanning(self):
        report, transcribe, translate = self.invoke([
            "శుభోదయం", BodhanRateLimit("rate_limited: Wait before retrying."), "శుభోదయం",
        ])
        self.assertEqual(report["status"], "rate_limited")
        self.assertEqual(transcribe.call_count, 2)
        self.assertEqual(translate.call_count, 1)
        clips = report["clip_selections"]["deliverable-1"]["clips"]
        self.assertEqual([clip["status"] for clip in clips], ["match", "assessment_failed", "pending"])
        self.assertIsNone(report["deliverables"][0]["artifact"])
        path, = report["partial_artifacts"]
        self.assertIn("Good morning", (self.root / "run" / path).read_text())
