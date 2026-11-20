from types import SimpleNamespace

import pytest

from bodhan_agent.planner import create_plan


def test_create_plan(monkeypatch):
    fake_content = (
        '{"steps": ['
        '{"description": "Extract text from image", "tool": "indic_ocr"},'
        '{"description": "Translate text to English", "tool": "indic_translate"}'
        ']}'
    )

    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=fake_content)
            )
        ]
    )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: fake_response
            )
        )
    )

    monkeypatch.setattr(
        "bodhan_agent.planner.get_llm_client",
        lambda: fake_client,
    )
    monkeypatch.setattr(
        "bodhan_agent.planner.get_model_name",
        lambda: "test-model",
    )

    plan = create_plan(
        task="Extract Bengali text from an image and translate it into English.",
        files=["sample_data/test_document.png"],
    )

    assert isinstance(plan, dict)
    assert "steps" in plan
    assert len(plan["steps"]) == 2
    assert plan["steps"][0]["tool"] == "indic_ocr"
    assert plan["steps"][1]["tool"] == "indic_translate"


def test_empty_task_rejected():
    with pytest.raises(ValueError, match="Task description cannot be empty"):
        create_plan(task="", files=[])