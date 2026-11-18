"""Agent building blocks: plan schema, refs, pricing, ledger, verifier, trace, write_text guard."""
from __future__ import annotations

import threading

import pytest
from pydantic import ValidationError

from bodhan_agent import executor, llm, verifier
from bodhan_agent.executor import Ledger, price_step, resolve_refs
from bodhan_agent.planner import Plan, Step
from bodhan_agent.trace import Trace, safe_args
from bodhan_mcp.core import costs
from bodhan_mcp.core import text as txt
from bodhan_mcp.core.errors import BodhanError


def plan_dict(**over):
    base = {
        "understanding": "Translate a file.",
        "steps": [
            {"id": "s1", "intent": "ocr", "tool": "ocr_document", "args": {"input_path": "a.pdf"}},
            {"id": "s2", "intent": "mt", "tool": "translate_text", "depends_on": ["s1"],
             "args": {"source_path": "$s1.markdown_path", "target_language": "en"}, "expected_chars": 500},
        ],
        "final_outputs": [{"role": "text", "from": "s2"}],
    }
    base.update(over)
    return base


def step(tool, args=None, expected=None, sid="s1"):
    return Step(id=sid, intent="x", tool=tool, args=args or {}, expected_chars=expected)


# --- plan schema -----------------------------------------------------------------------------

def test_valid_plan_parses():
    plan = Plan.model_validate(plan_dict())
    assert [s.id for s in plan.steps] == ["s1", "s2"] and plan.final_outputs[0].from_step == "s2"


@pytest.mark.parametrize(
    "over",
    [
        {"feasible": False, "steps": []},
        {"feasible": True, "steps": []},
        {"steps": [{"id": "s1", "intent": "a", "tool": "probe_media"}, {"id": "s1", "intent": "b", "tool": "probe_media"}]},
        {"steps": [{"id": "s1", "intent": "a", "tool": "probe_media", "depends_on": ["s9"]}]},
        {"steps": [{"id": "s1", "intent": "a", "tool": "whisper_transcribe"}]},
        {"final_outputs": [{"role": "text", "from": "s9"}]},
    ],
)
def test_incoherent_plans_are_rejected(over):
    with pytest.raises(ValidationError):
        Plan.model_validate(plan_dict(**over))


def test_infeasible_plan_with_refusal_is_accepted():
    plan = Plan.model_validate(plan_dict(feasible=False, steps=[], final_outputs=[], refusal="No French."))
    assert not plan.feasible


# --- references ------------------------------------------------------------------------------

def test_refs_resolve_including_inside_lists():
    results = {"s1": {"text_path": "/tmp/a.txt"}}
    out = resolve_refs({"source_path": "$s1.text_path", "inputs": ["$s1.text_path", "/x"], "n": 3}, results)
    assert out == {"source_path": "/tmp/a.txt", "inputs": ["/tmp/a.txt", "/x"], "n": 3}


def test_ref_to_a_step_that_has_not_run_or_a_missing_field_is_a_plan_error():
    results = {"s1": {"text_path": "/tmp/a.txt"}}
    for ref in ("$s2.text_path", "$s1.nope"):
        with pytest.raises(BodhanError) as exc:
            resolve_refs({"source_path": ref}, results)
        assert exc.value.code == "LLM_INVALID_PLAN"


# --- pricing ---------------------------------------------------------------------------------

def test_price_ocr_uses_selected_pages():
    inv = {"a.pdf": {"pages": 8}}
    assert price_step(step("ocr_document", {"input_path": "a.pdf", "pages": "2,5"}), inv) == pytest.approx(0.4)
    assert price_step(step("ocr_document", {"input_path": "a.pdf"}), inv) == pytest.approx(1.6)


def test_price_transcribe_from_inventory_duration():
    inv = {"a.wav": {"duration_s": 125.0}}
    assert price_step(step("transcribe_audio", {"audio_path": "a.wav"}), inv) == costs.asr_cost(125.0)


def test_price_generated_text_needs_expected_chars_or_a_readable_file(tmp_path):
    for tool in ("translate_text", "speak_text"):
        with pytest.raises(BodhanError) as exc:
            price_step(step(tool, {"source_path": "$s1.text_path", "text_path": "$s1.text_path"}), {})
        assert exc.value.code == "LLM_INVALID_PLAN"
        with pytest.raises(BodhanError) as exc:
            price_step(step(tool, {"source_path": str(tmp_path / "gone.txt"), "text_path": str(tmp_path / "gone.txt")}), {})
        assert exc.value.code == "LLM_INVALID_PLAN"
    assert price_step(step("translate_text", expected=10_000), {}) == costs.mt_estimate(10_000)
    assert price_step(step("speak_text", expected=1_000), {}) == pytest.approx(0.6)
    f = tmp_path / "t.txt"
    f.write_text("x" * 2000)
    assert price_step(step("speak_text", {"text_path": str(f)}), {}) == pytest.approx(costs.tts_cost(2000))


def test_free_tools_cost_nothing():
    for tool in ("probe_media", "detect_language", "write_text"):
        assert price_step(step(tool), {}) == 0.0


# --- ledger ----------------------------------------------------------------------------------

def test_ledger_refuses_beyond_cap_and_settle_frees_the_reservation():
    ledger = Ledger(1.0, 10)
    ledger.reserve(0.6)
    with pytest.raises(BodhanError) as exc:
        ledger.reserve(0.5)
    assert exc.value.code == "BUDGET_EXCEEDED"
    ledger.settle(0.6, 0.3)
    assert ledger.reserved == 0 and ledger.spent == pytest.approx(0.3)
    ledger.reserve(0.7)
    assert ledger.remaining() == pytest.approx(0, abs=1e-9)


def test_ledger_step_limit():
    ledger = Ledger(1.0, 2)
    ledger.take_step()
    ledger.take_step()
    with pytest.raises(BodhanError) as exc:
        ledger.take_step()
    assert exc.value.code == "STEP_LIMIT"


def test_ledger_is_thread_safe():
    ledger = Ledger(1.0, 100)
    ok, lock = [], threading.Lock()

    def worker():
        try:
            ledger.reserve(0.3)
            with lock:
                ok.append(1)
        except BodhanError:
            pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 3 and ledger.reserved <= 1.0 + 1e-9


# --- verifier --------------------------------------------------------------------------------

def test_similarity():
    assert verifier.similarity("one two three", "one two three") == 1.0
    assert verifier.similarity("one two three", "completely different") < 0.5


def test_digit_folding():
    assert txt.digits("\u0966\u0967 4a5") == ["0", "1", "4", "5"]  # Devanagari zero, one


def srt(tmp_path, body):
    p = tmp_path / "x.srt"
    p.write_text(body, encoding="utf-8")
    return p


GOOD = "1\n00:00:00,000 --> 00:00:02,000\nhello there\n\n2\n00:00:02,000 --> 00:00:04,000\nsecond cue"


def results(checks):
    return {c["check"]: c["result"] for c in checks}


def test_verify_srt_accepts_good_and_catches_overlap_and_long_lines(tmp_path):
    assert set(results(verifier.verify_srt(srt(tmp_path, GOOD))).values()) == {"passed"}
    overlap = GOOD.replace("00:00:02,000 --> 00:00:04,000", "00:00:01,000 --> 00:00:04,000")
    assert results(verifier.verify_srt(srt(tmp_path, overlap)))["monotonic"] == "failed"
    long_line = GOOD.replace("hello there", "x" * 60)
    assert results(verifier.verify_srt(srt(tmp_path, long_line)))["cues"] == "failed"


def test_derive_status():
    passed, failed = {"result": "passed"}, {"result": "failed"}
    assert verifier.derive_status([passed], any_artifact=False) == "failed"
    assert verifier.derive_status([], any_artifact=False) == "failed"
    assert verifier.derive_status([passed, failed], any_artifact=True) == "partial"
    assert verifier.derive_status([passed, {"result": "skipped"}], any_artifact=True) == "completed"
    assert verifier.derive_status([passed], any_artifact=True) == "completed"


def test_a_missing_artifact_fails_its_check(tmp_path):
    (check,) = verifier.verify_artifact(tmp_path / "gone.wav", "audio")
    assert check["result"] == "failed"


# --- trace -----------------------------------------------------------------------------------

def test_safe_args_drops_unknown_keys_and_truncates():
    out = safe_args({"source_path": "/a", "body": "secret prose", "api_key": "x", "instruction": "y" * 1000, "inputs": ["z" * 500]})
    assert set(out) == {"source_path", "instruction", "inputs"}
    assert len(out["instruction"]) < 400 and "1000 chars" in out["instruction"]
    assert len(out["inputs"][0]) < 400


# --- write_text guard ------------------------------------------------------------------------

@pytest.fixture
def fake_llm(monkeypatch):
    calls = []

    def chat(messages, max_tokens=4096, temperature=0.2):
        calls.append(messages)
        return llm.LlmReply(text=chat.draft, finish_reason="stop", prompt_chars=10, latency_ms=1)

    chat.draft = "A short English draft. It has two sentences."
    chat.calls = calls
    monkeypatch.setattr(llm, "chat", chat)
    return chat


def test_write_text_writes_english(tmp_path, fake_llm):
    src = tmp_path / "src.txt"
    src.write_text("English source.")
    res = executor._write_text({"inputs": [str(src)], "target_chars": 500, "out_dir": str(tmp_path)}, Trace(tmp_path / "t.jsonl"))
    assert (tmp_path / "draft_500.md").read_text() == fake_llm.draft and res.language == "en"


def test_write_text_rejects_an_indic_input_before_calling_the_llm(tmp_path, fake_llm):
    src = tmp_path / "src.txt"
    src.write_text("\u0b95\u0bb2\u0bcd\u0bb5\u0bbf")
    with pytest.raises(BodhanError) as exc:
        executor._write_text({"inputs": [str(src)], "out_dir": str(tmp_path)}, Trace(tmp_path / "t.jsonl"))
    assert exc.value.code == "LLM_WROTE_INDIC" and fake_llm.calls == []


def test_write_text_rejects_an_indic_draft(tmp_path, fake_llm):
    src = tmp_path / "src.txt"
    src.write_text("English source.")
    fake_llm.draft = "Hello \u0928\u092e\u0938\u094d\u0924\u0947"
    with pytest.raises(BodhanError) as exc:
        executor._write_text({"inputs": [str(src)], "out_dir": str(tmp_path)}, Trace(tmp_path / "t.jsonl"))
    assert exc.value.code == "LLM_WROTE_INDIC"
    assert not list(tmp_path.glob("draft_*"))
