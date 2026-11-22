"""Regressions: Marathi worksheet 429 + false-positive task completion.

No live model calls; tests exercise the production code with controlled substitutes.
"""
import json
import requests

from bodhan_agent.runner import AgentRunner
from bodhan_shared.gateway import Gateway
from bodhan_shared.store import Workspace
from bodhan_shared.tools import ToolHub
from bodhan_shared.errors import TaskError

WORKSHEET = """CLASSROOM WORKSHEET

Topic: Simple calculations and a shopping table

Instructions: Read the table, then answer the questions.

| Item | Quantity | Price per unit (INR) |
| --- | --- | --- |
| Notebook | 2 | 30 |
| Pencil | 3 | 5 |
| Eraser | 1 | 10 |

Questions

1. Calculate the total cost of all items.
2. The classroom opens at 9:00 AM.
3. Solve: $12 + 8 = \\underline{\\hspace{1.5cm}}$
4. Keep the numbers and the table structure unchanged.
"""

class UnsuccessfulBrain:
    def __init__(self):
        self.turn = 0

    def complete(self, messages):
        self.turn += 1
        if self.turn == 1:
            return {"content": None, "tool_calls": [{"id": "fail1", "type": "function", "function":
                    {"name": "translate_text", "arguments": json.dumps({"target_language": "mr", "input_path": "inputs/worksheet.md"})}}]}
        return {"content": "OCR succeeded but translation failed."}


def test_agent_marks_failed_translation_as_partial_even_if_ocr_exists(tmp_path):
    inputs = tmp_path / "inputs_original"
    inputs.mkdir()
    (inputs / "worksheet.md").write_text(WORKSHEET, encoding="utf-8")
    out = tmp_path / "run"
    hub = ToolHub(out)

    def failed_translation(target_language, text="", input_path="", output_path=""):
        raise TaskError("BODHAN_TRANSLATE_UNAVAILABLE", "Chat HTTP 429", retryable=True)
    hub.translate_text = failed_translation
    original = hub.call

    def call(tool, args):
        if tool == "translate_text":
            # The partial OCR output exists, but should not imply completion.
            hub.store.write("artifacts/worksheet.ocr.md", WORKSHEET)
        return original(tool, args)
    hub.call = call
    report = AgentRunner("Read worksheet and translate into Marathi", str(inputs), str(out),
                         brain=UnsuccessfulBrain(), hub=hub).run()
    assert report["status"] == "partial"
    assert "translation" in report["unmet_requirements"][0].lower()
    assert report["error"]["code"] == "BODHAN_TRANSLATE_UNAVAILABLE"


def test_document_batch_uses_one_bodhan_call_and_retains_structure(tmp_path):
    hub = ToolHub(tmp_path)
    observed = []

    def translate(text, language):
        observed.append((text, language))
        if language == "en":
            return "Backtranslation", False
        # Translating the complete document in one request retains table syntax.
        return text.replace("CLASSROOM WORKSHEET", "वर्ग कार्यपत्रक"), False

    hub.gateway.translate = translate
    r = hub.call("translate_text", {"target_language": "mr", "text": WORKSHEET})
    assert r["ok"], r
    assert observed[0] == (WORKSHEET, "mr")
    assert sum(language == "mr" for _, language in observed) == 1
    assert "| Pencil | 3 | 5 |" in r["translation"]
    assert "9:00" in r["translation"]
    assert r["qa"]["numbers_preserved"] and r["qa"]["math_preserved"]


def test_document_batch_rejects_lost_table_and_uses_safe_fallback(tmp_path):
    hub = ToolHub(tmp_path)
    calls = []

    def translate(text, language):
        calls.append(text)
        if len(text) > 200 and "|" in text:
            return "तालिका गायब", False
        return text, False

    hub.gateway.translate = translate
    result = hub.call("translate_text", {"target_language": "mr", "text": WORKSHEET})
    assert result["ok"], result
    assert "| Notebook | 2 | 30 |" in result["translation"]
    assert any(w["type"] == "document-batch-rejected" for w in result["qa"]["warnings"])
    assert len(calls) > 1


def test_429_from_bodhan_is_retryable_after_attempts(monkeypatch, tmp_path):
    from bodhan_shared import gateway as mod
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-key")
    monkeypatch.setenv("BODHAN_BASE_URL", "https://api.bodhan.ai")
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(mod.random, "uniform", lambda *_: 0)
    class Session:
        def __init__(self): self.count=0
        def post(self, *a, **kw):
            self.count += 1
            r = requests.Response()
            r.status_code = 429
            r._content = b'{"error":{"code":"429","message":"Maximum rate limit reached for the model"}}'
            return r
    sess=Session()
    gw=Gateway(Workspace(tmp_path), session=sess)
    try:
        gw.translate("Hello", "mr")
    except TaskError as exc:
        assert exc.code == "BODHAN_API_ERROR"
        assert exc.retryable
    else:
        raise AssertionError("429 must raise a retryable error")
    assert sess.count == 3
