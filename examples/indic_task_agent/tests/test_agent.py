"""The agent loop, driven by a scripted fake LLM over a mocked HTTP transport, with a fake Bodhan API."""

import json
from pathlib import Path

import httpx
import pytest
from bodhan_agent.__main__ import EXIT_CODES, main
from bodhan_agent.agent import Agent, RunConfig
from bodhan_agent.llm import LLMClient
from bodhan_mcp.bodhan import Budget
from conftest import make_client, needs_ffmpeg, wav_bytes

HINDI = "बैठक शुक्रवार तक स्थगित कर दी गई है।"


def fake_bodhan(state):
    def handler(req):
        path = req.url.path
        if path == "/translate":
            state["translate_calls"] = state.get("translate_calls", 0) + 1
            return httpx.Response(200, json={"translation": HINDI, "truncated": False, "usage": {"completion_tokens": 20}})
        if path == "/v1/audio/speech":
            state["speak_calls"] = state.get("speak_calls", 0) + 1
            state["last_speech"] = json.loads(req.content)["input"]
            return httpx.Response(200, content=wav_bytes(1.0), headers={"content-type": "audio/wav"})
        if path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": state.get("last_speech", "")})
        return httpx.Response(404, json={"detail": "Not Found"})

    return handler


def call(n, name, **args):
    return {"id": f"call_{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def last_tool_result(messages):
    for m in reversed(messages):
        if m["role"] == "tool":
            return json.loads(m["content"])
    return None


def scripted_llm(script, seen):
    """script(turn, messages) -> assistant message dict."""

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        msg = script(len(seen), body["messages"])
        return httpx.Response(200, json={"choices": [{"message": msg, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    return LLMClient(base_url="https://llm.test/v1", model="fake-model", api_key="k", transport=httpx.MockTransport(handler), sleep=lambda s: None)


def run_agent(tmp_path, script, *, files=(), max_steps=10, max_cost=20.0, state=None):
    state = {} if state is None else state
    seen = []
    client = make_client(tmp_path, fake_bodhan(state), budget=Budget(max_cost))
    config = RunConfig(task="test task", inputs=[Path(f) for f in files], out_dir=tmp_path / "out", max_steps=max_steps, max_cost_inr=max_cost)
    report = Agent(config, llm=scripted_llm(script, seen), client=client).run()
    return report, seen, state


def trace(tmp_path):
    return [json.loads(line) for line in (tmp_path / "out" / "trace.jsonl").read_text(encoding="utf-8").splitlines()]


@needs_ffmpeg
def test_multi_step_task_end_to_end(tmp_path):
    src = tmp_path / "inputs" / "note.txt"
    src.parent.mkdir()
    src.write_text("The meeting has been postponed to Friday.", encoding="utf-8")

    def script(turn, messages):
        if turn == 1:
            assert "note.txt" in messages[1]["content"]  # intake manifest reached the LLM
            return {"role": "assistant", "content": "Translate, then speak.", "tool_calls": [call(1, "translate_text", target_language="hi", path=str(src))]}
        if turn == 2:
            translated = last_tool_result(messages)["output_path"]
            return {"role": "assistant", "content": "", "tool_calls": [call(2, "speak_text", language="hi", path=translated, gender="female")]}
        audio = last_tool_result(messages)["audio_path"]
        return {"role": "assistant", "content": "", "tool_calls": [call(3, "finish", status="completed", answer="Done. The audio is AI-generated.",
                                                                     assumptions=["Used the default female Hindi voice."], artifacts=[audio])]}

    report, seen, _ = run_agent(tmp_path, script, files=[src.parent])
    out = tmp_path / "out"
    assert report["status"] == "completed" and report["all_checks_passed"]
    assert report["steps_used"] == 3 and report["cost_inr"] > 0
    assert report["verification"][0]["passed"] is True and report["verification"][0]["tool"] == "speak_text"
    assert all((out / name).exists() for name in ("result.md", "report.json", "trace.jsonl"))
    assert list((out / "artifacts").glob("*.wav"))
    assert not (out / ".work").exists()
    kinds = [e["kind"] for e in trace(tmp_path)]
    assert kinds[0] == "start" and "intake" in kinds and kinds.count("tool") == 3 and kinds[-1] == "finish"
    result_md = (out / "result.md").read_text(encoding="utf-8")
    assert "AI-generated" in result_md and "Used the default female Hindi voice." in result_md
    # The LLM saw the same tool schemas an MCP client sees, plus the agent-only tools.
    names = {t["function"]["name"] for t in seen[0]["tools"]}
    assert {"transcribe_audio", "translate_text", "speak_text", "ocr_document", "finish", "save_english_text"} <= names


def test_llm_written_indic_text_is_refused(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [call(1, "speak_text", language="hi", text="यह मैंने खुद लिखा है")]}
        assert last_tool_result(messages)["error"]["code"] == "indic_text_not_from_tool"
        return {"role": "assistant", "content": "", "tool_calls": [call(2, "finish", status="failed", answer="Could not proceed.")]}

    report, _, state = run_agent(tmp_path, script)
    assert report["status"] == "failed"
    assert "speak_calls" not in state  # nothing was sent to Indic-Speak


def test_finish_answer_with_invented_indic_text_is_refused_then_fixed(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [call(1, "finish", status="completed", answer="उत्तर: सब ठीक है")]}
        assert last_tool_result(messages)["error"]["code"] == "indic_text_not_from_tool"
        return {"role": "assistant", "content": "", "tool_calls": [call(2, "finish", status="completed", answer="All fine.")]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["status"] == "completed"


def test_quoting_tool_output_is_allowed(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [call(1, "translate_text", target_language="hi", text="The meeting is postponed.")]}
        quoted = last_tool_result(messages)["translation"]
        return {"role": "assistant", "content": "", "tool_calls": [call(2, "finish", status="completed", answer=f"Translation: {quoted}")]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["status"] == "completed"


def test_impossible_task_ends_honestly_without_outputs(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [call(1, "list_languages", capability="translation")]}
        return {"role": "assistant", "content": "", "tool_calls": [call(2, "finish", status="impossible",
                                                                     answer="French is not a supported translation target, so no output was made.")]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["status"] == "impossible" and report["artifacts"] == []
    assert EXIT_CODES["impossible"] == 0
    assert "French is not a supported" in (tmp_path / "out" / "result.md").read_text(encoding="utf-8")


def test_impossible_with_outputs_is_rejected(tmp_path):
    (tmp_path / "out" / "artifacts").mkdir(parents=True)
    fake = tmp_path / "out" / "artifacts" / "x.md"
    fake.write_text("x")

    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [call(1, "finish", status="impossible", answer="No.", artifacts=[str(fake)])]}
        assert last_tool_result(messages)["error"]["code"] == "bad_arguments"
        return {"role": "assistant", "content": "", "tool_calls": [call(2, "finish", status="impossible", answer="Cannot be done.")]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["status"] == "impossible"


def test_budget_stops_the_run_cleanly(tmp_path):
    def script(turn, messages):
        translate = call(turn, "translate_text", target_language="hi", text="A long enough sentence to cost money.")
        return {"role": "assistant", "content": "", "tool_calls": [translate]}

    report, seen, state = run_agent(tmp_path, script, max_cost=0.0000001)
    assert report["status"] == "budget_exhausted" and report["stopped_by"] == "runtime"
    assert len(seen) == 1 and "translate_calls" not in state
    assert EXIT_CODES["budget_exhausted"] == 2


def test_step_limit(tmp_path):
    def script(turn, messages):
        return {"role": "assistant", "content": "", "tool_calls": [call(turn, "list_languages", capability="ocr")]}

    report, seen, _ = run_agent(tmp_path, script, max_steps=3)
    assert report["status"] == "step_limit_reached" and len(seen) == 3


def test_paths_outside_inputs_and_output_are_refused(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [call(1, "read_text", path="/etc/hosts")]}
        assert last_tool_result(messages)["error"]["code"] == "path_not_allowed"
        return {"role": "assistant", "content": "", "tool_calls": [call(2, "finish", status="failed", answer="No access.")]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["status"] == "failed"


def test_finish_must_list_real_artifacts(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [call(1, "finish", status="completed", answer="Done", artifacts=["/nope/out.mp3"])]}
        assert last_tool_result(messages)["error"]["code"] == "bad_artifacts"
        return {"role": "assistant", "content": "", "tool_calls": [call(2, "finish", status="failed", answer="Nothing was made.")]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["status"] == "failed"


def test_parallel_calls_each_get_their_own_result(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [call(1, "list_languages", capability="ocr"),
                                                                        call(2, "list_languages", capability="translation")]}
        tools = [m for m in messages if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tools] == ["call_1", "call_2"]
        assert json.loads(tools[0]["content"])["capability"] == "ocr"
        return {"role": "assistant", "content": "", "tool_calls": [call(3, "finish", status="completed", answer="ok")]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["status"] == "completed"


def test_bad_and_unknown_arguments_are_reported(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "list_languages", "arguments": "{not json"}},
                call(2, "translate_text", target_language="hi", text="x", colour="blue"),
                call(3, "no_such_tool"),
            ]}
        codes = [json.loads(m["content"])["error"]["code"] for m in messages if m["role"] == "tool"]
        assert codes == ["bad_arguments", "bad_arguments", "unknown_tool"]
        return {"role": "assistant", "content": "", "tool_calls": [call(4, "finish", status="failed", answer="stop")]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["status"] == "failed"


def test_save_english_text_refuses_indic(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [
                call(1, "save_english_text", filename="s.md", content="नमस्ते"),
                call(2, "save_english_text", filename="../../escape.md", content="An English summary.")]}
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        assert results[0]["error"]["code"] == "indic_text_refused"
        saved = Path(results[1]["path"])
        assert saved.parent.name == "artifacts" and saved.suffix == ".md"
        return {"role": "assistant", "content": "", "tool_calls": [call(3, "finish", status="completed", answer="ok", artifacts=[str(saved)])]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["status"] == "completed"
    assert report["verification"][0]["passed"] is None  # English written by the LLM has no model check


def test_rerun_into_same_folder_clears_old_outputs(tmp_path):
    def script(turn, messages):
        return {"role": "assistant", "content": "", "tool_calls": [call(turn, "finish", status="completed", answer="ok")]}

    run_agent(tmp_path, script)
    stale = tmp_path / "out" / "artifacts" / "old.md"
    stale.parent.mkdir(exist_ok=True)
    stale.write_text("old")
    run_agent(tmp_path, script)
    assert not stale.exists()


def test_cli_without_llm_config_fails_honestly(tmp_path, monkeypatch):
    for var in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.setenv(var, "")
    monkeypatch.chdir(tmp_path)  # no .env here
    code = main(["--task", "Translate this", "--out", str(tmp_path / "out"), "--cache-dir", str(tmp_path / "cache")])
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert code == 1 and report["status"] == "failed"
    assert "not configured" in (tmp_path / "out" / "result.md").read_text(encoding="utf-8")


def test_llm_client_drops_temperature_when_rejected():
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if "temperature" in body:
            return httpx.Response(400, text="Unsupported parameter: temperature")
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]})

    llm = LLMClient(base_url="https://llm.test/v1", model="m", api_key="k", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert llm.chat([{"role": "user", "content": "x"}], [])["content"] == "hi"
    assert "temperature" in bodies[0] and "temperature" not in bodies[1]


def test_llm_client_waits_out_a_busy_provider():
    statuses = iter([503, 503, 429, 200])
    waits = []

    def handler(req):
        status = next(statuses)
        if status == 200:
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
        headers = {"retry-after": "7"} if status == 429 else {}
        return httpx.Response(status, json={"error": {"message": "high demand"}}, headers=headers)

    llm = LLMClient(base_url="https://llm.test/v1", model="m", api_key="k", transport=httpx.MockTransport(handler), sleep=waits.append)
    assert llm.chat([{"role": "user", "content": "x"}], [])["content"] == "ok"
    assert len(waits) == 3 and waits[0] < waits[1] and waits[2] == 7.0


def test_llm_client_does_not_retry_a_bad_request():
    from bodhan_agent.llm import LLMError

    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(401, text="bad key")

    llm = LLMClient(base_url="https://llm.test/v1", model="m", api_key="k", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "x"}], [])
    assert len(calls) == 1


def test_llm_client_rejects_placeholder_config():
    from bodhan_agent.llm import LLMError

    with pytest.raises(LLMError):
        LLMClient(base_url="<your-llm-base-url>", model="<m>", api_key="<k>")


def test_agent_cannot_write_subtitles_or_code_files(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [
                call(1, "save_english_text", filename="out.srt", content="1\nHello"),
                call(2, "save_english_text", filename="notes.md", content="1\n00:00:00,000 --> 00:00:02,000\nHello"),
                call(3, "save_english_text", filename="build.py", content="print(1)"),
            ]}
        codes = [json.loads(m["content"])["error"]["code"] for m in messages if m["role"] == "tool"]
        assert codes == ["bad_arguments"] * 3
        return {"role": "assistant", "content": "", "tool_calls": [call(4, "finish", status="failed", answer="stop")]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["status"] == "failed" and not list((tmp_path / "out" / "artifacts").glob("*"))


def test_reading_an_output_later_keeps_its_check(tmp_path):
    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [call(1, "translate_text", target_language="hi", text="Hello there.")]}
        if turn == 2:
            return {"role": "assistant", "content": "", "tool_calls": [call(2, "read_text", path=last_tool_result(messages)["output_path"])]}
        path = last_tool_result(messages)["path"]
        return {"role": "assistant", "content": "", "tool_calls": [call(3, "finish", status="completed", answer="ok", artifacts=[path])]}

    report, _, _ = run_agent(tmp_path, script)
    assert report["verification"][0]["passed"] is True and report["verification"][0]["tool"] == "translate_text"


def test_read_text_reaches_the_llm_in_full_and_loops_get_a_warning(tmp_path):
    src = tmp_path / "inputs" / "long.md"
    src.parent.mkdir()
    src.write_text("word " * 2000, encoding="utf-8")  # 10000 characters

    def script(turn, messages):
        if turn == 1:
            return {"role": "assistant", "content": "", "tool_calls": [call(1, "read_text", path=str(src))]}
        result = last_tool_result(messages)
        if turn == 2:
            assert len(result["text"]) == 10000 and result["next_offset"] is None
        if turn < 6:
            if turn == 5:
                assert "_warning" not in result  # three turns in a row: no warning yet
            return {"role": "assistant", "content": "", "tool_calls": [call(turn, "list_languages", capability="ocr")]}
        assert "_warning" in result  # turns 2, 3, 4 and 5 all called list_languages
        return {"role": "assistant", "content": "", "tool_calls": [call(9, "finish", status="failed", answer="stop")]}

    report, _, _ = run_agent(tmp_path, script, files=[src.parent])
    assert report["status"] == "failed"
