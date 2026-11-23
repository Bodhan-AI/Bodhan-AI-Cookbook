import json
from types import SimpleNamespace

from bodhan_agent import agent


class FakeCall:
    def __init__(self, index, name, args):
        self.id = f"call_{index}"
        self.function = SimpleNamespace(name=name, arguments=json.dumps(args))

    def model_dump(self, exclude_none=True):
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


class ScriptedLLM:
    """Stands in for the real LLM: plays back a fixed list of tool calls."""

    def __init__(self, plan):
        self.plan = list(plan)
        self.count = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        name, args = self.plan.pop(0)
        self.count += 1
        message = SimpleNamespace(content="", tool_calls=[FakeCall(self.count, name, args)])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_multi_step_task_runs_verifies_and_reports(tmp_path, monkeypatch):
    inputs = tmp_path / "in"
    inputs.mkdir()
    (inputs / "page.png").write_bytes(b"fake image")
    out = tmp_path / "out"
    out.mkdir()
    ocr_md, translated_md, wav, spoken = out / "ocr.md", out / "tr.md", out / "a.wav", out / "a.txt"

    def ocr_document(file_path, **kwargs):
        ocr_md.write_text("मेहनत का कोई विकल्प नहीं है।", encoding="utf-8")
        return {"ok": True, "text": "मेहनत का कोई विकल्प नहीं है।", "markdown_path": str(ocr_md),
                "cost_inr": 0.2, "untrusted_content": True}

    def translate_file(text_path, target_language):
        translated_md.write_text("உழைப்புக்கு மாற்றே இல்லை.", encoding="utf-8")
        return {"ok": True, "translated_path": str(translated_md), "preview": "உழைப்புக்கு மாற்றே இல்லை.",
                "structure_preserved": True, "cost_inr": 0.0}

    def speak_text(language, text="", text_path=None, **kwargs):
        wav.write_bytes(b"RIFF....fake audio")
        spoken.write_text("உழைப்புக்கு மாற்றே இல்லை.", encoding="utf-8")
        return {"ok": True, "audio_path": str(wav), "spoken_text_path": str(spoken), "language": language,
                "duration_seconds": 1.5, "cost_inr": 0.0}

    def transcribe_audio(audio_path, language=None):
        return {"ok": True, "text": "உழைப்புக்கு மாற்றே இல்லை", "cost_inr": 0.0}

    functions = {f.__name__: f for f in (ocr_document, translate_file, speak_text, transcribe_audio)}
    llm = ScriptedLLM([
        ("ocr_document", {"file_path": str(inputs / "page.png")}),
        ("translate_file", {"text_path": str(ocr_md), "target_language": "ta"}),
        ("speak_text", {"text_path": str(translated_md), "language": "ta", "gender": "female"}),
        ("finish", {"status": "completed", "summary": "Done. The audio is AI-generated.",
                    "artifacts": [str(wav)]}),
    ])
    monkeypatch.setattr(agent, "make_client", lambda: (llm, "fake-model"))
    monkeypatch.setattr(agent, "load_tools", lambda: ([], functions))

    report = agent.run_task("Read the text in this image aloud in Tamil.", str(inputs), str(out), 10, 5.0, 30)

    assert report["status"] == "completed"
    assert report["speech_checks"][0]["checked"] and report["speech_checks"][0]["passed"]
    assert report["total_cost_inr"] == 0.2
    assert (out / "artifacts" / "a.wav").exists()
    events = [json.loads(line)["event"] for line in (out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events.count("tool") == 3 and "verify" in events and "finish" in events


def test_unsupported_language_is_refused_before_any_model_call(tmp_path, monkeypatch):
    def must_not_run():
        raise AssertionError("the LLM must not be called for an unsupported language")

    monkeypatch.setattr(agent, "make_client", must_not_run)
    report = agent.run_task("Translate this video into French.", None, str(tmp_path / "out"), 5, 1.0, 10)

    assert report["status"] == "impossible"
    assert list((tmp_path / "out" / "artifacts").iterdir()) == []
    assert report["guardrail_events"][0]["guardrail"] == "language_gate"