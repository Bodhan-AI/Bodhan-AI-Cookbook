from __future__ import annotations

from types import SimpleNamespace

import pytest

from bodhan_agent.planner import AgentPlanner, TaskPlan
from bodhan_agent.tool_catalog import AGENT_TOOLS, build_tool_catalog
from bodhan_mcp.models import ToolError


def test_catalog_lists_only_executable_agent_tools_and_current_configuration():
    catalog = build_tool_catalog(
        {
            "BODHAN_TRANSCRIBE_KEY": "transcribe-secret",
            "BODHAN_TRANSLATE_KEY": "translate-secret",
            "LLM_API_KEY": "llm-secret",
        }
    )

    tools = {tool["name"]: tool for tool in catalog["tools"]}
    assert set(tools) == set(AGENT_TOOLS)
    assert tools["transcribe"]["available"] is True
    assert tools["translate"]["available"] is True
    assert tools["speak"]["available"] is False
    assert tools["speak"]["required_models"] == ["speak", "transcribe"]
    assert tools["ocr"]["available"] is False
    assert tools["summarize_english"]["available"] is True
    assert tools["summarize_english"]["required_models"] == ["llm"]
    assert "secret" not in repr(catalog)
    assert "hi" in catalog["supported_languages"]
    assert catalog["features"]["audio_transcription"]["available"] is True
    assert catalog["features"]["task_level_summarization"]["available"] is True
    assert catalog["features"]["duration_targeted_speech"]["implemented"] is True
    assert catalog["features"]["speech_synthesis"]["available"] is False
    assert catalog["features"]["spoken_language_identification"]["implemented"] is True
    assert catalog["features"]["spoken_language_identification"]["available"] is True
    assert catalog["features"]["timestamped_transcription"]["implemented"] is True
    assert "Ishita" in catalog["verified_voices"]

    fully_configured = build_tool_catalog(
        {"BODHAN_SPEAK_KEY": "speak-secret", "BODHAN_TRANSCRIBE_KEY": "transcribe-secret"}
    )
    configured_speak = next(tool for tool in fully_configured["tools"] if tool["name"] == "speak")
    assert configured_speak["available"] is True


def test_catalog_tool_schemas_match_plan_validation_contract():
    for name, tool in AGENT_TOOLS.items():
        assert tool.name == name
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["additionalProperties"] is False
        assert tool.output_schema["type"] == "object"
        assert tool.input_suffixes
        assert all(output_type for output_type in tool.output_types.values())
        assert tool.model in {"transcribe", "translate", "speak", "ocr", "llm", "local"}


def test_planner_prompt_uses_injected_catalog_instead_of_fixed_tool_list(monkeypatch):
    observed: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    observed.update(kwargs)
                    return SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content=(
                                        '{"version":1,"steps":[{"id":"translate_text",'
                                        '"tool":"translate","arguments":{'
                                        '"target_language":"ta","text":"Hi"}}],'
                                        '"deliverables":[{"name":"translated_text",'
                                        '"step_id":"translate_text","output":"translation",'
                                        '"media_type":"text","description":"Translated text"}],'
                                        '"refusal":null}'
                                    )
                                )
                            )
                        ],
                    )

    monkeypatch.setattr("bodhan_agent.planner.OpenAI", FakeOpenAI)
    catalog = build_tool_catalog({"BODHAN_TRANSLATE_KEY": "present"})
    planner = AgentPlanner(timeout_s=1, tool_catalog=catalog)

    plan = planner.plan_task(
        "Translate the selected text",
        ["notes.txt"],
        input_manifest=[{"file_path": "notes.txt", "extension": ".txt"}],
    )

    assert plan == TaskPlan(
        steps=[
            {
                "id": "translate_text",
                "tool": "translate",
                "arguments": {"target_language": "ta", "text": "Hi"},
            }
        ],
        deliverables=[
            {
                "name": "translated_text",
                "step_id": "translate_text",
                "output": "translation",
                "media_type": "text",
                "description": "Translated text",
            }
        ],
    )
    system_prompt = observed["messages"][0]["content"]
    assert '"name": "translate"' in system_prompt
    assert '"available": true' in system_prompt
    assert '"spoken_language_identification"' in system_prompt
    assert '"version":1' in system_prompt
    assert "$ref" in system_prompt
    assert "for_each" in system_prompt
    assert "never write, translate, transcribe, or quote Indian-language content" in system_prompt
    assert "1. {'tool': 'transcribe'" not in system_prompt
    assert "Available Input Files: ['notes.txt']" in observed["messages"][1]["content"]
    assert '"extension": ".txt"' in observed["messages"][1]["content"]


def test_planner_parses_specific_capability_refusal(monkeypatch):
    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content=(
                                        '{"steps":[],"refusal":{"code":"CAPABILITY_UNAVAILABLE",'
                                        '"message":"Timed transcription is unavailable.",'
                                        '"hint":"Use a deployment with timed transcription.",'
                                        '"capability":"timestamped_transcription"}}'
                                    )
                                )
                            )
                        ],
                    )

    monkeypatch.setattr("bodhan_agent.planner.OpenAI", FakeOpenAI)
    planner = AgentPlanner(timeout_s=1, tool_catalog=build_tool_catalog({}))

    planned = planner.plan_task("Create subtitles", ["audio.wav"])

    assert planned.steps == []
    assert planned.refusal == {
        "code": "CAPABILITY_UNAVAILABLE",
        "message": "Timed transcription is unavailable.",
        "hint": "Use a deployment with timed transcription.",
        "capability": "timestamped_transcription",
    }


def test_planner_english_summarizer_accepts_english_and_rejects_indic_output(monkeypatch):
    observed: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    observed.update(kwargs)
                    return SimpleNamespace(
                        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content="A concise English summary.")
                            )
                        ],
                    )

    monkeypatch.setattr("bodhan_agent.planner.OpenAI", FakeOpenAI)
    planner = AgentPlanner(timeout_s=1, tool_catalog=build_tool_catalog({}))

    summary = planner.summarize_english("OCR source text", max_words=20)

    assert summary == "A concise English summary."
    assert planner.last_summary_usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }
    assert "English only" in observed["messages"][0]["content"]
    assert "at most 20 English words" in observed["messages"][1]["content"]

    class IndicOutputOpenAI(FakeOpenAI):
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content="தமிழ் சுருக்கம்")
                            )
                        ],
                    )

    monkeypatch.setattr("bodhan_agent.planner.OpenAI", IndicOutputOpenAI)
    planner = AgentPlanner(timeout_s=1, tool_catalog=build_tool_catalog({}))
    with pytest.raises(ToolError, match="non-English-script"):
        planner.summarize_english("OCR source text")


def test_planner_rejects_unversioned_composable_plan(monkeypatch):
    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content=(
                                        '{"steps":[{"id":"translate","tool":"translate",'
                                        '"arguments":{"target_language":"hi","text":"Hello"}}],'
                                        '"deliverables":[],"refusal":null}'
                                    )
                                )
                            )
                        ],
                    )

    monkeypatch.setattr("bodhan_agent.planner.OpenAI", FakeOpenAI)
    planner = AgentPlanner(timeout_s=1, tool_catalog=build_tool_catalog({}))

    with pytest.raises(ToolError, match="Task planning failed"):
        planner.plan_task("Translate text", [])


def test_planner_extracts_fenced_json_and_falls_back_without_response_format(monkeypatch):
    calls: list[dict] = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    if "response_format" in kwargs:
                        raise TypeError("response_format unsupported")
                    return SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content=(
                                        "<think>reasoning</think>\n"
                                        "Here is the plan:\n```json\n"
                                        '{"version":1,"needs_followup":false,"steps":[{"id":"s1",'
                                        '"tool":"translate","arguments":{"target_language":"ta",'
                                        '"text":"Hello"}}],"deliverables":[{"name":"out",'
                                        '"step_id":"s1","output":"translation",'
                                        '"media_type":"text","description":"Tamil text"}],'
                                        '"refusal":null}\n```'
                                    )
                                )
                            )
                        ],
                    )

    monkeypatch.setattr("bodhan_agent.planner.OpenAI", FakeOpenAI)
    planner = AgentPlanner(
        timeout_s=1, 
        tool_catalog=build_tool_catalog({"BODHAN_TRANSLATE_KEY": "k"})
    )

    plan = planner.plan_task("Translate", [])

    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert plan.steps[0]["id"] == "s1"