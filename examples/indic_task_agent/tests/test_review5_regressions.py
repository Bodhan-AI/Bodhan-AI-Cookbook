"""Regression tests for review round 5 (commit 3f41986)."""

import pytest
from fakes import FakeBodhan, ScriptedChat
from test_agent_e2e import args, last_ok, read_outputs

from bodhan_agent.cli import run
from bodhan_agent.llm import PlannerLLM
from bodhan_mcp.languages import detect_language
from bodhan_mcp.registry import invoke
from bodhan_mcp.tools import TOOL_BY_NAME


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch):
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")


# 1. French made of words English shares ("a", "on", "et") is not English; real English still is
@pytest.mark.parametrize("text", ["On a un chat et on a un chien.", "Il a un chat.", "Elle est à la maison avec on a."])
def test_french_with_shared_short_words_is_not_english(text):
    assert detect_language(text).language != "en"


@pytest.mark.parametrize("text", ["The meeting is on Friday.", "Fill in the blanks.", "The summary covers the entire page.", "Not at ten as we said before",
                                  "Please bring two notebooks to class and a pen."])
def test_short_english_is_still_english(text):
    assert detect_language(text).language == "en"


async def test_french_is_sent_to_the_model_not_copied(make_ctx):
    fake = FakeBodhan()
    r, err = await invoke(TOOL_BY_NAME["translate_text"], make_ctx(fake), {"text": "On a un chat et on a un chien.", "target_language": "en"})
    assert not err and fake.calls["translate"] >= 1


# 2. a summary written from a translation keeps the translation's source, so a source-bound contract is met
async def test_summary_with_source_paths_satisfies_a_source_bound_contract(tmp_path):
    a = tmp_path / "notice.txt"
    a.write_text("The library will be closed on Monday for repairs.", encoding="utf-8")
    plan = ("record_plan", {"steps": ["summarise in Hindi"], "deliverables": [{"kind": "translation", "language": "hi", "source": "notice.txt"}]})
    summary = lambda m: [("write_text", {"name": "summary_en.md", "content": "The library is closed on Monday.",
                                         "source_paths": [str(a)]})]
    chat = ScriptedChat([[plan], summary, lambda m: [("translate_text", {"path": last_ok(m, "path"), "target_language": "hi"})],
                         lambda m: [("finish", {"status": "completed", "final_answer": "Summary translated.", "deliverables": [last_ok(m, "path")]})]])
    await run(args(tmp_path, "Summarise the notice in Hindi", [a]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert not [e for e in trace if e["type"] == "finish_rejected"]
    assert rep["status"] in ("completed", "completed_with_warnings")


async def test_source_paths_must_exist(make_ctx):
    r, err = await invoke(TOOL_BY_NAME["write_text"], make_ctx(FakeBodhan()), {"name": "s.md", "content": "x", "source_paths": ["nope.txt"]})
    assert err and r["error"]["code"] == "file_not_found"


# 3. priced planner retries are budgeted and failed attempts are charged
class _Flaky:
    """Fails the first `fail` planner requests with a 503 after 'sending' them, then behaves like `inner`."""

    class _E(Exception):
        status_code = 503

    def __init__(self, inner, fail=1):
        self.inner, self.fail, self.sent = inner, fail, 0
        self.chat = self
        self.completions = self

    async def create(self, **kw):
        self.sent += 1
        if self.sent <= self.fail:
            raise self._E("upstream unavailable")
        return await self.inner.create(**kw)


def _priced(tmp_path, txt, budget):
    return args(tmp_path, "Detect the language", [txt], max_cost_inr=budget, llm_input_inr_per_1m=1000.0, llm_output_inr_per_1m=1000.0)


async def test_failed_planner_attempt_before_a_success_is_charged(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees grow slowly.", encoding="utf-8")
    chat = ScriptedChat([[("finish", {"status": "unsupported", "final_answer": "Stopping."})]])
    await run(_priced(tmp_path, txt, 40.0), transport=FakeBodhan().transport, llm=PlannerLLM(client=_Flaky(chat), model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    charged = [e for e in trace if e["type"] == "llm_uncertain"]
    reply = [e for e in trace if e["type"] == "llm_call"]
    assert len(charged) == 1 and charged[0]["attempts"] == 1 and charged[0]["cost_inr"] > 0
    assert rep["cost"]["llm_inr"] == pytest.approx(charged[0]["cost_inr"] + reply[0]["cost_inr"], abs=1e-4)


async def test_planner_retry_is_not_sent_beyond_the_budget(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees grow slowly.", encoding="utf-8")
    flaky = _Flaky(ScriptedChat([]), fail=99)
    # one estimated planner call (about ₹13 at ₹1000 per 1M tokens) fits in ₹20, two do not
    await run(_priced(tmp_path, txt, 20.0), transport=FakeBodhan().transport, llm=PlannerLLM(client=flaky, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert flaky.sent == 1 and rep["stop_reason"] == "llm_error"
    assert rep["cost"]["total_counted_inr"] <= 20.0 + 1e-9
