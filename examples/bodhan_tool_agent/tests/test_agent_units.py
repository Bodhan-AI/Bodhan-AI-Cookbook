"""Unit tests for the agent's guards, budget, trace, LLM fallback parsing and verification ledger."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_agent.budget import Budget
from bodhan_agent.guards import CallGuard, PathPolicy, canonical, check_english_only, indic_text_keys
from bodhan_agent.llm import parse_tool_calls_from_text, to_text_protocol
from bodhan_agent.report import clean_name
from bodhan_agent.trace import TraceWriter, summarize_arguments
from bodhan_agent.verify import Ledger

KNOWN = {"translate_text", "finish", "submit_plan"}


# ------------------------------------------------------------------ guards

def _guard(tmp_path: Path) -> CallGuard:
    policy = PathPolicy()
    policy.add_root(tmp_path / "inputs")
    policy.add_root(tmp_path / "work")
    return CallGuard(policy)


def test_inline_indic_text_is_rejected(tmp_path: Path) -> None:
    v = _guard(tmp_path).check("translate_text", {"text": "नमस्ते दुनिया", "target_language": "en"})
    assert v is not None and v.code == "indic_text_not_allowed"
    assert "path" in v.message


def test_inline_english_text_is_allowed(tmp_path: Path) -> None:
    assert _guard(tmp_path).check("translate_text", {"text": "Hello world", "target_language": "ta"}) is None


def test_indic_in_path_argument_is_fine(tmp_path: Path) -> None:
    p = tmp_path / "inputs" / "नमस्ते.txt"
    assert _guard(tmp_path).check("translate_text", {"path": str(p), "target_language": "en"}) is None


def test_indic_nested_in_other_args_is_rejected() -> None:
    assert indic_text_keys({"expected_text": "தமிழ்", "audio_path": "x.mp3"}) == ["expected_text"]
    assert indic_text_keys({"answer": "fine", "assumptions": ["ok", "বাংলা"]}) == ["assumptions"]


def test_path_outside_run_is_rejected(tmp_path: Path) -> None:
    v = _guard(tmp_path).check("read_text", {"path": str(tmp_path / "elsewhere" / "secret.txt")})
    assert v is not None and v.code == "path_not_allowed"


def test_traversal_out_of_root_is_rejected(tmp_path: Path) -> None:
    sneaky = str(tmp_path / "work" / ".." / "elsewhere.txt")
    assert _guard(tmp_path).check("read_text", {"path": sneaky}).code == "path_not_allowed"


def test_speech_language_mismatch_with_english_file(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    english = tmp_path / "work" / "summary_en.md"
    guard.known_text_language[canonical(english)] = "en"
    v = guard.check("synthesize_speech", {"path": str(english), "language": "ta"})
    assert v is not None and v.code == "language_mismatch"
    assert guard.check("synthesize_speech", {"path": str(english), "language": "en"}) is None
    assert guard.check("synthesize_speech", {"text": "Hello there", "language": "hi"}).code == "language_mismatch"


def test_speech_between_indic_languages_not_second_guessed(tmp_path: Path) -> None:
    # Script-based detection cannot tell Hindi from Marathi reliably, so only English mismatches are blocked.
    guard = _guard(tmp_path)
    f = tmp_path / "inputs" / "para.txt"
    guard.known_text_language[canonical(f)] = "hi"
    assert guard.check("synthesize_speech", {"path": str(f), "language": "mr"}) is None


def test_english_only_check() -> None:
    assert check_english_only("A short English summary.") is None
    assert check_english_only("Summary: ನಮಸ್ಕಾರ") is not None


# ------------------------------------------------------------------ budget

def test_budget_steps_and_cost() -> None:
    b = Budget(max_steps=2, max_cost_inr=1.0)
    b.use_step()
    assert b.steps_left == 1 and not b.steps_exhausted
    b.charge(0.4)
    assert abs(b.remaining_inr - 0.6) < 1e-9 and b.exhausted is None
    b.use_step()
    assert b.exhausted == "steps"


def test_budget_cost_exhaustion() -> None:
    b = Budget(max_steps=10, max_cost_inr=0.5)
    b.charge(0.5)
    assert b.cost_exhausted and b.exhausted == "cost"
    b2 = Budget(max_steps=10, max_cost_inr=5)
    b2.mark_cost_exhausted()
    assert b2.exhausted == "cost"
    assert b2.snapshot()["remaining_inr"] == 5


# ------------------------------------------------------------------ trace

def test_trace_lines_and_truncation(tmp_path: Path) -> None:
    t = TraceWriter(tmp_path / "trace.jsonl")
    long_text = "word " * 500
    t.log("tool", turn=1, tool="translate_text",
          arguments={"text": long_text, "path": "C:/very/long/" + "p" * 400 + ".txt"},
          result_summary={"ok": True}, latency_s=1.23456, cost_inr=0.01, cache_hits=1)
    t.log("verify", turn=1, tool="verify_translation", cost_inr=0.02)
    t.close()
    lines = [json.loads(x) for x in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["step"] for x in lines] == [1, 2]
    first = lines[0]
    assert set(first) >= {"step", "ts", "type", "tool", "arguments", "result_summary", "latency_s", "cost_inr",
                          "cumulative_cost_inr", "cache_hits", "llm_tokens"}
    assert len(first["arguments"]["text"]) < 400
    assert first["arguments"]["path"].endswith(".txt")  # paths are never shortened
    assert lines[1]["cumulative_cost_inr"] == 0.03


def test_trace_rejects_unknown_type(tmp_path: Path) -> None:
    t = TraceWriter(tmp_path / "t.jsonl")
    try:
        t.log("bogus")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    finally:
        t.close()


def test_summarize_arguments_keeps_lists_of_paths() -> None:
    out = summarize_arguments({"artifacts": ["a" * 500], "answer": "b" * 500})
    assert out["artifacts"][0] == "a" * 500
    assert len(out["answer"]) < 500


# --------------------------------------------------------- JSON fallback

def test_parse_plain_json_object() -> None:
    calls = parse_tool_calls_from_text('Sure. {"tool": "finish", "arguments": {"status": "failed", "answer": "x"}}',
                                       KNOWN)
    assert len(calls) == 1 and calls[0].name == "finish" and calls[0].arguments["status"] == "failed"
    assert calls[0].from_text


def test_parse_fenced_block_and_alternate_keys() -> None:
    text = 'I will translate.\n```json\n{"name": "translate_text", "parameters": {"path": "a.txt", ' \
           '"target_language": "hi"}}\n```'
    calls = parse_tool_calls_from_text(text, KNOWN)
    assert [c.name for c in calls] == ["translate_text"]
    assert calls[0].arguments == {"path": "a.txt", "target_language": "hi"}


def test_parse_array_openai_shape_and_string_arguments() -> None:
    text = json.dumps([
        {"type": "function", "function": {"name": "submit_plan", "arguments": json.dumps({"goal": "g"})}},
        {"tool": "translate_text", "arguments": {"text": "Hi", "target_language": "ta"}},
    ])
    calls = parse_tool_calls_from_text(text, KNOWN)
    assert [c.name for c in calls] == ["submit_plan", "translate_text"]
    assert calls[0].arguments == {"goal": "g"}


def test_parse_ignores_unknown_tools_and_prose() -> None:
    assert parse_tool_calls_from_text('{"tool": "rm_rf", "arguments": {}}', KNOWN) == []
    assert parse_tool_calls_from_text("No JSON here {just braces}", KNOWN) == []
    assert parse_tool_calls_from_text("", KNOWN) == []


def test_parse_braces_inside_strings() -> None:
    text = '{"tool": "finish", "arguments": {"status": "completed", "answer": "set {x} and }"}}'
    calls = parse_tool_calls_from_text(text, KNOWN)
    assert calls and calls[0].arguments["answer"] == "set {x} and }"


def test_text_protocol_rewrites_tool_history() -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "finish", "arguments": "{\"status\": \"x\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{\"ok\": true}"},
    ]
    tools = [{"type": "function", "function": {"name": "finish", "description": "End.", "parameters": {}}}]
    out = to_text_protocol(msgs, tools)
    assert all(m["role"] in ("system", "user", "assistant") for m in out)
    assert "finish" in out[0]["content"] and "```json" in out[2]["content"]
    assert out[3]["role"] == "user" and "[result of finish]" in out[3]["content"]


# ------------------------------------------------------------------ ledger

def test_ledger_translation_requirement_and_check(tmp_path: Path) -> None:
    led = Ledger()
    src = str(tmp_path / "t.json")
    out_txt, out_json = str(tmp_path / "t.en.txt"), str(tmp_path / "t.en.json")
    led.record_tool("translate_text", {"path": src, "target_language": "en"},
                    {"output_path": out_txt, "segments_path": out_json, "cost_inr": 0.01})
    req = led.requirement_for(out_json)
    assert req is not None and req.tool == "verify_translation"
    assert req.arguments["translation_path"] == out_txt and req.arguments["source_path"] == src
    # Verifying either file of the pair satisfies both.
    led.record_tool("verify_translation", {"translation_path": out_json, "target_language": "en"},
                    {"passed": True, "issues": []})
    assert led.status(out_txt).passed and led.status(out_json).passed


def test_ledger_subtitles_inherit_translation_check(tmp_path: Path) -> None:
    led = Ledger()
    seg = str(tmp_path / "tr.json")
    led.record_tool("translate_text", {"path": str(tmp_path / "in.json"), "target_language": "en"},
                    {"output_path": str(tmp_path / "tr.txt"), "segments_path": seg})
    led.record_tool("make_subtitles", {"segments_path": seg}, {"subtitle_path": str(tmp_path / "tr.srt")})
    req = led.requirement_for(str(tmp_path / "tr.srt"))
    assert req is not None and req.kind == "translation"


def test_ledger_subtitles_from_transcript_need_no_check(tmp_path: Path) -> None:
    led = Ledger()
    tr = str(tmp_path / "a.transcript.json")
    led.record_tool("transcribe_audio", {"path": str(tmp_path / "a.wav"), "language": "auto"},
                    {"transcript_path": tr, "text_path": str(tmp_path / "a.txt"), "language": "hi"})
    led.record_tool("make_subtitles", {"segments_path": tr}, {"subtitle_path": str(tmp_path / "a.srt")})
    assert led.requirement_for(str(tmp_path / "a.srt")) is None
    assert led.get(tr).language == "hi"


def test_ledger_audio_verified_by_synthesis(tmp_path: Path) -> None:
    led = Ledger()
    audio = str(tmp_path / "s.mp3")
    led.record_tool("synthesize_speech", {"path": str(tmp_path / "x.ta.md"), "language": "ta"},
                    {"audio_path": audio, "verification": {"passed": True, "mean_cer": 0.05, "failed_chunks": [],
                                                           "transcript_path": str(tmp_path / "s.check.json")}})
    assert led.status(audio).passed
    assert led.get(str(tmp_path / "s.check.json")).kind == "other"


def test_ledger_audio_without_verification_needs_verify_speech(tmp_path: Path) -> None:
    led = Ledger()
    audio = str(tmp_path / "s.wav")
    led.record_tool("synthesize_speech", {"path": str(tmp_path / "x.md"), "language": "bn", "verify": False},
                    {"audio_path": audio, "verification": {"passed": True}})
    assert led.status(audio) is None
    req = led.requirement_for(audio)
    assert req.tool == "verify_speech" and req.arguments["expected_path"].endswith("x.md")


def test_clean_name() -> None:
    assert clean_name("my summary (final).ta.md") == "my_summary_final_.ta.md"
    assert clean_name("..") == "artifact"


def test_provider_fields_on_tool_calls_are_echoed_back():
    from openai.types.chat import ChatCompletion

    from bodhan_agent.agent import TaskAgent
    from bodhan_agent.llm import OpenAICompatLLM

    resp = ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "inspect_file", "arguments": "{\"path\": \"a.txt\"}"},
                            "extra_content": {"google": {"thought_signature": "sig"}}}]}}],
    })
    parsed = OpenAICompatLLM._parse(object.__new__(OpenAICompatLLM), resp, [{"function": {"name": "inspect_file"}}])
    msg = TaskAgent._assistant_message(parsed)
    assert msg["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "sig"}}
    assert msg["tool_calls"][0]["function"]["name"] == "inspect_file"
