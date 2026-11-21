"""Planner portability: providers other than the one the demos used (Groq, Gemini, small open models)."""

import json

import pytest
from fakes import FakeBodhan, ScriptedChat, _Obj
from test_agent_e2e import args, read_outputs

from bodhan_agent.cli import run
from bodhan_agent.llm import LLMError, PlannerLLM, clean_tool_name, decode_arguments

FINISH_TOOL = [{"type": "function", "function": {"name": "finish", "parameters": {"type": "object", "properties": {}}}}]


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch):
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")


class Err(Exception):
    def __init__(self, status, message, body=None):
        super().__init__(message)
        self.status_code, self.body = status, body


class Endpoint:
    """A fake provider: `behave(kwargs)` returns a response object or raises."""

    def __init__(self, behave):
        self.behave, self.sent = behave, []
        self.chat = self.completions = self

    async def create(self, **kw):
        self.sent.append(kw)
        return self.behave(kw)


def reply(content=None, calls=(), msg_extra=None, call_extra=None):
    tcs = [_Obj(id=f"t{i}", function=_Obj(name=n, arguments=a, model_extra={}), model_extra=dict(call_extra or {})) for i, (n, a) in enumerate(calls)]
    msg = _Obj(content=content, tool_calls=tcs or None, model_extra=dict(msg_extra or {}))
    return _Obj(choices=[_Obj(message=msg, finish_reason="tool_calls" if tcs else "stop")], usage=_Obj(prompt_tokens=5, completion_tokens=2))


async def test_provider_extras_are_echoed_and_tool_call_content_is_null():
    ep = Endpoint(lambda kw: reply(None, [("finish", "{}")], msg_extra={"reasoning_content": "thinking"},
                                   call_extra={"extra_content": {"google": {"thought_signature": "sig"}}}))
    r = await PlannerLLM(client=ep, model="m").chat([{"role": "user", "content": "x"}], FINISH_TOOL)
    m = r.assistant_message
    assert m["content"] is None and m["reasoning_content"] == "thinking"
    assert m["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "sig"
    assert "refusal" not in m and "annotations" not in m


async def test_an_empty_turn_is_sent_back_without_its_reasoning():
    def gateway(kw):  # mimics an OpenAI-compatible endpoint that refused this message shape
        for m in kw["messages"]:
            if m.get("role") == "assistant" and not m.get("content") and not m.get("tool_calls") and m.get("reasoning_content"):
                raise Err(400, "Error code: 400 - Expected a(n) 'messages' array element to be an object.")
        return reply("", [], msg_extra={"reasoning_content": "nothing to say"}) if len(ep.sent) == 1 else reply(None, [("finish", "{}")])

    ep = Endpoint(gateway)
    llm = PlannerLLM(client=ep, model="m")
    first = await llm.chat([{"role": "user", "content": "x"}], FINISH_TOOL)
    assert first.assistant_message == {"role": "assistant", "content": ""}
    msgs = [{"role": "user", "content": "x"}, first.assistant_message, {"role": "user", "content": "Continue by calling a tool."}]
    second = await llm.chat(msgs, FINISH_TOOL)
    assert second.tool_calls[0].name == "finish"


async def test_a_groq_tool_use_failed_generation_is_recovered():
    def behave(kw):
        raise Err(400, "Error code: 400 - tool_use_failed", {"error": {"code": "tool_use_failed",
                  "failed_generation": '<function=finish>{"status": "unsupported", "final_answer": "No."}</function>'}})

    r = await PlannerLLM(client=Endpoint(behave), model="m").chat([{"role": "user", "content": "x"}], FINISH_TOOL)
    assert r.tool_calls[0].name == "finish" and r.tool_calls[0].arguments["status"] == "unsupported"


async def test_a_rejected_tool_choice_is_dropped_and_a_rejected_tools_falls_back_to_text():
    def no_tool_choice(kw):
        if "tool_choice" in kw:
            raise Err(400, "Error code: 400 - 'tool_choice' is not supported")
        return reply(None, [("finish", "{}")])

    llm = PlannerLLM(client=Endpoint(no_tool_choice), model="m")
    assert (await llm.chat([{"role": "user", "content": "x"}], FINISH_TOOL)).tool_calls and llm.no_tool_choice

    def no_tools(kw):
        if "tools" in kw:
            raise Err(400, "Error code: 400 - this model does not support tools")
        return reply('Sure: {"name": "finish", "arguments": {"status": "failed", "final_answer": "x"}} done')

    ep = Endpoint(no_tools)
    llm = PlannerLLM(client=ep, model="m")
    r = await llm.chat([{"role": "system", "content": "s"}, {"role": "user", "content": "x"}], FINISH_TOOL)
    assert llm.text_protocol and r.tool_calls[0].name == "finish" and r.fallback_parsed
    last = ep.sent[-1]
    assert "tools" not in last and sum(m["role"] == "system" for m in last["messages"]) == 1


def test_arguments_and_tool_names_as_open_models_send_them():
    assert decode_arguments('"{\\"path\\": \\"a.txt\\"}"') == ({"path": "a.txt"}, None)  # JSON inside a JSON string
    assert decode_arguments(None) == ({}, None) and decode_arguments("[1]")[1]
    assert clean_tool_name("finish<|channel|>commentary", {"finish"}) == "finish"
    assert clean_tool_name("functions.read_text", {"read_text"}) == "read_text"


async def test_null_for_an_optional_argument_means_not_given(make_ctx, tmp_path):
    from bodhan_mcp.registry import invoke
    from bodhan_mcp.tools import TOOL_BY_NAME

    r, err = await invoke(TOOL_BY_NAME["translate_text"], make_ctx(FakeBodhan()), {"text": "Hello friends.", "target_language": "hi",
                                                                                  "source_language": None, "output_name": None})
    assert not err, r


async def test_a_too_long_context_is_compacted_and_retried(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("The library opens at eight. " * 40, encoding="utf-8")
    plan = ("record_plan", {"steps": ["read"], "deliverables": [{"kind": "answer"}]})
    reads = [[("read_text", {"path": str(txt)})] for _ in range(8)]
    calls = {"n": 0}
    inner = ScriptedChat([[plan], *reads, [("finish", {"status": "completed", "final_answer": "It opens at eight."})]])

    async def create(**kw):
        calls["n"] += 1
        if calls["n"] == 10 and not any("compacted" in (m.get("content") or "") for m in kw["messages"]):
            raise Err(400, "Error code: 400 - This model's maximum context length is 8192 tokens")
        return await inner.create(**kw)

    ep = Endpoint(None)
    ep.create = create
    await run(args(tmp_path, "When does the library open?", [txt]), transport=FakeBodhan().transport, llm=PlannerLLM(client=ep, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert any(e["type"] == "context_compacted" for e in trace) and rep["status"] in ("completed", "completed_with_warnings")


async def test_a_plain_text_final_answer_is_not_lost(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("The library opens at eight.", encoding="utf-8")
    plan = ("record_plan", {"steps": ["read", "answer"], "deliverables": [{"kind": "answer"}]})
    answer = "The library opens at eight o'clock."
    chat = ScriptedChat([[plan], [("read_text", {"path": str(txt)})], answer, answer, answer])
    await run(args(tmp_path, "When does the library open?", [txt]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    # kept, but never claimed complete: plain text carries no status (an explicit {"status": ...} would be honoured)
    assert rep["status"] == "partial" and "eight" in rep["final_answer"] and any(e["type"] == "finish_salvaged" for e in trace)


def test_bare_finish_arguments_written_as_text_are_understood():
    from bodhan_agent.llm import _fallback_tool_call
    from bodhan_agent.loop import openai_tools

    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in openai_tools(None)}
    text = '{\n  "status": "completed",\n  "final_answer": "The worksheet has been translated into Marathi."\n}'
    call = _fallback_tool_call(text, set(schemas), schemas)
    assert call is not None and call.name == "finish" and call.arguments["status"] == "completed"


async def test_inline_text_copied_from_an_artifact_keeps_its_lineage(make_ctx, tmp_path):
    from bodhan_mcp.registry import invoke
    from bodhan_mcp.runtime import Artifact
    from bodhan_mcp.tools import TOOL_BY_NAME

    ctx = make_ctx(FakeBodhan())
    ocr = ctx.artifacts_dir / "worksheet_ocr.md"
    ocr.parent.mkdir(parents=True, exist_ok=True)
    ocr.write_text("Worksheet 4: The planets of our solar system and their moons.", encoding="utf-8")
    ctx.register(Artifact(ocr, "ocr", "ocr_document", "en", "indic-ocr", None, origin=["worksheet_photo.jpg"]))
    r, err = await invoke(TOOL_BY_NAME["translate_text"], ctx, {"text": "Worksheet 4: The planets of our solar system and their moons.",
                                                                "target_language": "mr"})
    assert not err and ctx.artifacts[r["path"]].origin == ["worksheet_photo.jpg"]


async def test_tools_rejection_mentioning_tool_choice_still_reaches_the_text_protocol():
    def behave(kw):
        if "tools" in kw:
            raise Err(400, "Error code: 400 - tools and tool_choice are unsupported for this model")
        return reply('{"name": "finish", "arguments": {"status": "failed", "final_answer": "x"}}')

    llm = PlannerLLM(client=Endpoint(behave), model="m")
    r = await llm.chat([{"role": "user", "content": "x"}], FINISH_TOOL)
    assert llm.text_protocol and r.tool_calls[0].name == "finish"


async def test_an_unrelated_error_mentioning_function_does_not_switch_protocol():
    def behave(kw):
        raise Err(400, "Error code: 400 - invalid value for messages[2].function_call")

    llm = PlannerLLM(client=Endpoint(behave), model="m", max_attempts=2)
    with pytest.raises(LLMError):
        await llm.chat([{"role": "user", "content": "x"}], FINISH_TOOL)
    assert not llm.text_protocol


async def test_an_echoed_field_the_provider_refuses_is_dropped():
    calls = {"n": 0}

    def behave(kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return reply(None, [("finish", "{}")], msg_extra={"diagnostic": "internal"})
        if any("diagnostic" in m for m in kw["messages"]):
            raise Err(400, "Error code: 400 - property 'diagnostic' is not allowed in messages")
        return reply(None, [("finish", "{}")])

    llm = PlannerLLM(client=Endpoint(behave), model="m")
    msgs = [{"role": "user", "content": "x"}]
    first = await llm.chat(msgs, FINISH_TOOL)
    msgs += [first.assistant_message, {"role": "tool", "tool_call_id": first.tool_calls[0].id, "content": "{}"}]
    second = await llm.chat(msgs, FINISH_TOOL)
    assert second.tool_calls and "diagnostic" not in json.dumps(msgs) and llm.notes


async def test_a_groq_parsing_failure_is_sampled_again_not_treated_as_configuration(monkeypatch):
    # seen live: "400 - Parsing failed. The model generated output that could not
    # be parsed" ended the run at once, because a 400 without "tool" in its text counted as a configuration error
    monkeypatch.setattr("bodhan_agent.llm.retry_wait", lambda exc, attempt: 0)

    def behave(kw):
        if len(ep.sent) == 1:
            raise Err(400, "Error code: 400 - Parsing failed. The model generated output that could not be parsed. Please adjust "
                      "your prompt. See 'failed_generation' for more details.", {"error": {"failed_generation": "<|channel|>analysis..."}})
        return reply(None, [("finish", '{"status": "completed", "final_answer": "Done."}')])

    ep = Endpoint(behave)
    r = await PlannerLLM(client=ep, model="m").chat([{"role": "user", "content": "x"}], FINISH_TOOL)
    assert len(ep.sent) == 2 and r.tool_calls[0].name == "finish"


async def test_a_plain_bad_request_is_still_not_retried():
    def behave(kw):
        raise Err(400, "Error code: 400 - model 'nope' does not exist")

    ep = Endpoint(behave)
    with pytest.raises(LLMError):
        await PlannerLLM(client=ep, model="m").chat([{"role": "user", "content": "x"}], FINISH_TOOL)
    assert len(ep.sent) == 1
