"""End-to-end regression: unsupported targets never invoke the planner or tools."""
from bodhan_agent.runner import AgentRunner


class MustNotCallLLM:
    def complete(self, messages):
        raise AssertionError("unsupported target should stop before LLM planning")


def test_mp4_unsupported_target_fails_with_correct_explanation(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"synthetic-media-placeholder")
    out = tmp_path / "run"
    report = AgentRunner(
        "Translate this video into Spanish and save a Spanish output video.",
        str(source), str(out), brain=MustNotCallLLM(), max_cost_inr=1,
    ).run()
    assert report["status"] == "not_completed"
    assert report["error"]["code"] == "UNSUPPORTED_LANGUAGE"
    assert report["tool_events"] == []
    assert report["artifacts"] == []
    assert report["estimated_bodhan_cost_inr"] == 0.0
    assert "Spanish" in report["unmet_requirements"][0]
    assert "OCR-only" not in report["unmet_requirements"][0]
    text = (out / "result.md").read_text(encoding="utf-8")
    assert "Unsupported target language: Spanish" in text
    assert "French speech" not in text
