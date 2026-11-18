from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from bodhan_agent import __main__ as agent
from bodhan_agent.errors import ToolError
from bodhan_agent.settings import Settings


class FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = "function"
        self.function = SimpleNamespace(name=name, arguments=json.dumps(arguments))

    def model_dump(self):
        return {"id": self.id, "type": self.type, "function": {"name": self.function.name, "arguments": self.function.arguments}}


class FakeCompletions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return next(self.responses)


def _response(content=None, tool_call=None):
    message = SimpleNamespace(content=content, tool_calls=[tool_call] if tool_call else None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class MultiStepExecutor:
    calls = []

    def __init__(self, settings, artifacts_dir):
        self.artifacts_dir = artifacts_dir
        self.calls = []
        MultiStepExecutor.calls = self.calls

    def call(self, name, arguments, *, max_cost_inr, spent_inr):
        self.calls.append(name)
        if name == "ocr_document":
            return {"text": "Clinic opens at 9 AM.", "cost_inr": 0.2, "pages": 1}
        if name == "translate_text":
            return {"translation": "மருத்துவமனை காலை 9 மணிக்கு திறக்கும்.", "cost_inr": 0.001, "verification": {"passed": True}}
        raise AssertionError(f"Unexpected tool: {name}")


def test_agent_runs_two_tools_and_writes_trace_report_and_result(tmp_path: Path, monkeypatch):
    image_file = tmp_path / "notice.png"
    image_file.write_bytes(b"synthetic image")
    fake_settings = Settings("transcribe", "translate", "speak", "ocr", "https://llm.example/v1", "test-model", "llm-key")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, **kwargs: fake_settings))
    responses = [
        _response(tool_call=FakeToolCall("call-1", "ocr_document", {"file_ref": str(image_file)})),
        _response(tool_call=FakeToolCall("call-2", "translate_text", {"text": "Clinic opens at 9 AM.", "target_language": "ta"})),
        _response(content="Tamil translation: மருத்துவமனை காலை 9 மணிக்கு திறக்கும்."),
    ]
    completions = FakeCompletions(responses)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(agent, "_load_client", lambda settings: fake_client)
    monkeypatch.setattr(agent, "_verify_translation_with_llm", lambda client, model, pairs: {"status": "full", "passed": True, "reason": "all source meaning represented"})
    monkeypatch.setattr(agent, "ToolExecutor", MultiStepExecutor)

    code = agent.run_task("Read the notice and translate it into Tamil", [str(image_file)], tmp_path / "run", 5, 1.0)

    report = json.loads((tmp_path / "run" / "report.json").read_text(encoding="utf-8"))
    result_text = (tmp_path / "run" / "result.md").read_text(encoding="utf-8")
    trace = [json.loads(line) for line in (tmp_path / "run" / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    tool_steps = [item for item in trace if item["type"] == "tool"]
    assert code == 0
    assert report["status"] == "completed"
    assert report["steps"] == 2
    assert report["bodhan_cost_inr"] == 0.201
    assert MultiStepExecutor.calls == ["ocr_document", "translate_text"]
    assert [item["tool"] for item in tool_steps] == ["ocr_document", "translate_text"]
    assert "Tamil translation" in result_text
    assert "மருத்துவமனை காலை 9 மணிக்கு திறக்கும்." in result_text
    answer_artifact = tmp_path / "run" / "artifacts" / "task_result.md"
    assert answer_artifact.is_file()
    assert "மருத்துவமனை காலை 9 மணிக்கு திறக்கும்." in answer_artifact.read_text(encoding="utf-8")
    assert "artifacts\\task_result.md" in report["artifacts"]
    assert trace[-1]["type"] == "final"


def test_agent_stops_cleanly_when_cost_budget_is_exhausted(tmp_path: Path, monkeypatch):
    image_file = tmp_path / "source.txt"
    image_file.write_text("Hello", encoding="utf-8")
    fake_settings = Settings("transcribe", "translate", "speak", "ocr", "https://llm.example/v1", "test-model", "llm-key")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, **kwargs: fake_settings))
    completion = FakeCompletions([_response(tool_call=FakeToolCall("call-1", "translate_text", {"text": "Hello", "target_language": "hi"}))])
    monkeypatch.setattr(agent, "_load_client", lambda settings: SimpleNamespace(chat=SimpleNamespace(completions=completion)))

    class OverBudgetExecutor:
        def __init__(self, settings, artifacts_dir):
            pass

        def call(self, *args, **kwargs):
            raise ToolError("Estimated call would exceed remaining budget", "budget_exceeded")

    monkeypatch.setattr(agent, "ToolExecutor", OverBudgetExecutor)
    code = agent.run_task("Translate Hello into Hindi", [str(image_file)], tmp_path / "budget-run", 5, 0.001)

    report = json.loads((tmp_path / "budget-run" / "report.json").read_text(encoding="utf-8"))
    assert code == 1
    assert report["status"] == "stopped_cost_budget"
    assert report["steps"] == 1
    assert "budget" in (tmp_path / "budget-run" / "result.md").read_text(encoding="utf-8").lower()


def test_expected_unsupported_language_is_a_completed_limitation(tmp_path: Path, monkeypatch):
    source = tmp_path / "notice.txt"
    source.write_text("Clinic opens at nine.", encoding="utf-8")
    fake_settings = Settings("transcribe", "translate", "speak", "ocr", "https://llm.example/v1", "test-model", "llm-key")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, **kwargs: fake_settings))
    responses = [
        _response(tool_call=FakeToolCall("call-1", "translate_text", {"text": "Clinic opens at nine.", "target_language": "fr"})),
        _response(content="French is unsupported, so I did not produce a translation."),
    ]
    monkeypatch.setattr(
        agent,
        "_load_client",
        lambda settings: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(responses))),
    )

    class UnsupportedExecutor:
        def __init__(self, settings, artifacts_dir):
            pass

        def call(self, name, arguments, *, max_cost_inr, spent_inr):
            raise ToolError("Unsupported language code 'fr'.", "unsupported_language")

    monkeypatch.setattr(agent, "ToolExecutor", UnsupportedExecutor)
    code = agent.run_task("Translate this notice to French", [str(source)], tmp_path / "unsupported", 3, 1.0)

    report = json.loads((tmp_path / "unsupported" / "report.json").read_text(encoding="utf-8"))
    assert code == 0
    assert report["status"] == "completed"
    assert report["expected_limitations"] == [{"code": "unsupported_language", "message": "Unsupported language code 'fr'."}]
    assert report["verification"][0]["status"] == "expected_limitation"
    assert (tmp_path / "unsupported" / "artifacts" / "task_result.md").is_file()
