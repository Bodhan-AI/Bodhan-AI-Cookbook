"""Tests for passing translated text by reference between bodhan_translate and bodhan_speak.
Ensures that the LLM never has to retype Indian-language text.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from bodhan_agent.agent import BodhanTaskAgent
from bodhan_mcp.types import TranslateOutput, SpeakOutput


def test_translate_speak_by_reference(tmp_path: Path):
    """Verify that bodhan_speak accepts text_artifact_id without requiring text."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    files_dir = tmp_path / "files"
    files_dir.mkdir()

    agent = BodhanTaskAgent(
        task="Translate summary and recite in Tamil",
        files_dir=files_dir,
        out_dir=out_dir,
        max_cost_inr=5.0,
    )

    # 1. Mock translate_text
    mock_trans_res = TranslateOutput(
        translated_text="இது ஒரு முக்கியமான சுகாதார அறிக்கை.",
        target_language="ta",
        estimated_tokens=25,
        cost_inr=0.005,
        cached=True,
    )

    # 2. Mock synthesize_speech
    mock_speak_res = SpeakOutput(
        audio_path=str(out_dir / "artifacts" / "summary_ta.mp3"),
        duration_seconds=5.2,
        character_count=35,
        cost_inr=0.02,
        cached=True,
    )

    with patch("bodhan_agent.agent.translate_text", return_value=mock_trans_res), \
         patch("bodhan_agent.agent.synthesize_speech", return_value=mock_speak_res) as mock_speak:

        # Step 1: Execute bodhan_translate
        tr_json, cost, summary = agent.dispatch_tool(
            "bodhan_translate",
            {"text": "This is an important health report.", "target_language_code": "ta"}
        )
        tr_data = json.loads(tr_json)
        assert "text_artifact_id" in tr_data
        ref_id = tr_data["text_artifact_id"]
        assert "translated_ta.txt" in ref_id

        # Verify artifact file was actually created on disk
        ref_file = out_dir / ref_id
        assert ref_file.exists()
        assert ref_file.read_text(encoding="utf-8") == "இது ஒரு முக்கியமான சுகாதார அறிக்கை."

        # Step 2: Execute bodhan_speak with text_artifact_id (NO text argument!)
        spk_json, spk_cost, spk_summary = agent.dispatch_tool(
            "bodhan_speak",
            {
                "text_artifact_id": ref_id,
                "language": "ta",
                "voice": "Kavitha",
                "output_filename": "artifacts/summary_ta.mp3"
            }
        )

        # Assert synthesize_speech was called with the exact loaded text
        mock_speak.assert_called_once()
        _, kwargs = mock_speak.call_args
        assert kwargs["text"] == "இது ஒரு முக்கியமான சுகாதார அறிக்கை."
        assert kwargs["language"] == "ta"
        assert kwargs["voice"] == "Kavitha"
