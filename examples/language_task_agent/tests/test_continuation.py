"""Safe paused/resumed autonomous evidence, not fresh-run mocks labeled live."""

import json
from decimal import Decimal

import httpx
import pytest

from bodhan_agent.checkpoint import restore, save
from bodhan_agent.runner import execute
from bodhan_core.gateway import Gateway
from bodhan_core.runtime import ToolError
from conftest import input_ref
from test_agent import tool_response


@pytest.mark.asyncio
async def test_resume_pending_speech_keeps_planner_decision_budget_cache_and_check(
    run, fake, monkeypatch
):
    source = input_ref(run, "The class starts at nine.")
    task = "Read the English text aloud."
    planner_count = 0

    def planner(request):
        nonlocal planner_count
        planner_count += 1
        if planner_count == 1:
            return tool_response("synthesize", {"source": source, "language": "en"}, 1)
        data = json.loads(json.loads(request.content)["messages"][-1]["content"].split("\n", 1)[1])
        return tool_response(
            "finish",
            {
                "status": "completed",
                "explanation": "Audio verified.",
                "outputs": [data["ref"]],
                "assumptions": [],
            },
            2,
        )

    fake.planner = planner
    pause_once = True

    def boundary(headroom=0):
        nonlocal pause_once
        if headroom and len(fake.requests) >= 2 and pause_once:
            pause_once = False
            raise ToolError("execution_window", "Test safe boundary.")

    monkeypatch.setattr(run, "check_window", boundary)
    partial = await execute(task, run, Gateway(run, httpx.MockTransport(fake)))
    assert partial["status"] == "partial" and partial["explanation"].startswith("paused:")
    assert planner_count == 1 and len(run.checks) == 1 and not run.checks[0]["passed"]
    prior_cost, prior_steps = run.cost, run.steps
    resumed = restore(run.input_root, run.output, run.limits, task)
    report = await execute(task, resumed, Gateway(resumed, httpx.MockTransport(fake)))
    assert report["status"] == "completed" and report["resume_count"] == 1
    assert report["execution_kind"] == "paused_resumed_autonomous"
    assert resumed.cost >= prior_cost and resumed.steps > prior_steps
    assert planner_count == 2  # no new planning before the same pending speech tool resumed
    assert len(resumed.checks) == 1 and resumed.checks[0]["passed"]
    assert len([r for r in fake.requests if r.url.path.endswith("speech")]) == 1
    assert report["total_seconds"] >= partial["total_seconds"]
    content = (run.output / ".continuation.json").read_text()
    assert "mock-only-not-a-real-key" not in content


@pytest.mark.parametrize("change", ["task", "model", "input", "limits", "path"])
def test_resume_refuses_mismatched_context(run, monkeypatch, change):
    input_ref(run)
    task = "A task."
    save(run, task, [])
    if change == "task":
        task = "Another task."
    elif change == "model":
        monkeypatch.setenv("LLM_MODEL", "different-model")
    elif change == "input":
        next(run.input_root.glob("*")).write_text("Changed content")
    elif change == "limits":
        run.limits.max_cost = Decimal("999")
    elif change == "path":
        p = run.output / ".continuation.json"
        s = json.loads(p.read_text())
        s["refs"][next(iter(s["refs"]))] = "/etc/passwd"
        p.write_text(json.dumps(s))
    with pytest.raises(ToolError):
        restore(run.input_root, run.output, run.limits, task)


def test_resume_cannot_reset_active_time_steps_or_cost(run):
    input_ref(run)
    run.cost = Decimal("1.25")
    run.steps = 7
    run.elapsed_before = 299
    save(run, "A task.", [])
    resumed = restore(run.input_root, run.output, run.limits, "A task.")
    assert resumed.cost == Decimal("1.25") and resumed.steps == 7
    assert 0 < resumed.remaining_time() < 1
    resumed.elapsed_before = 301
    with pytest.raises(ToolError, match="Run time budget"):
        resumed.remaining_time()


def test_resume_checkpoint_is_one_use(run):
    input_ref(run)
    save(run, "A task.", [])
    restore(run.input_root, run.output, run.limits, "A task.")
    with pytest.raises(OSError):
        restore(run.input_root, run.output, run.limits, "A task.")


def test_post_terminal_correction_preserves_original_budgets(run, tmp_path):
    from bodhan_agent.checkpoint import carry_budget, run_context

    report = tmp_path / "prior.json"
    report.write_text(
        json.dumps({"total_cost_inr": "0.7628", "total_seconds": 127.834, "steps": 16, "calls": []})
    )
    (tmp_path / "run_context.json").write_text(json.dumps(run_context(run)))
    carry_budget(run, report)
    assert run.cost == Decimal("0.7628") and run.steps == 16
    assert 171 < run.remaining_time() < 173
    assert run.elapsed() >= 127.834


def test_carry_refuses_unverified_changed_or_second_lineage(run, tmp_path):
    from bodhan_agent.checkpoint import carry_budget, run_context

    report = tmp_path / "prior.json"
    prior = {"total_cost_inr": "0.5", "total_seconds": 10, "steps": 2, "calls": []}
    report.write_text(json.dumps(prior))
    with pytest.raises(OSError):
        carry_budget(run, report)
    context = run_context(run)
    context["limits"]["max_steps"] += 1
    (tmp_path / "run_context.json").write_text(json.dumps(context))
    with pytest.raises(ToolError):
        carry_budget(run, report)
    (tmp_path / "run_context.json").write_text(json.dumps(run_context(run)))
    prior["lineage"] = {"kind": "budget_carried_correction"}
    report.write_text(json.dumps(prior))
    with pytest.raises(ToolError):
        carry_budget(run, report)


async def test_cache_only_never_dispatches_uncached_bodhan(run, monkeypatch):
    import httpx
    from bodhan_core.gateway import Gateway

    monkeypatch.setenv("BODHAN_CACHE_ONLY", "1")
    requests = []
    g = Gateway(run, httpx.MockTransport(lambda r: requests.append(r)))
    try:
        with pytest.raises(ToolError, match="cache-only"):
            await g.request(
                model="indic-translate",
                url="https://example.com/v1/chat/completions",
                key_env="BODHAN_TRANSLATE_KEY",
                payload={"input": "new"},
                reserve=Decimal("0.024"),
            )
        assert not requests and not run.calls and run.cost == 0
    finally:
        await g.close()


def test_resume_refuses_switching_cache_only_into_paid_mode(run, monkeypatch):
    input_ref(run)
    monkeypatch.setenv("BODHAN_CACHE_ONLY", "1")
    save(run, "A task.", [])
    monkeypatch.setenv("BODHAN_CACHE_ONLY", "0")
    with pytest.raises(ToolError):
        restore(run.input_root, run.output, run.limits, "A task.")

@pytest.mark.parametrize("setting,value", [
    ("LLM_REASONING_EFFORT", "medium"),
    ("LLM_MAX_OUTPUT_TOKENS", "4096"),
])
def test_resume_rejects_changed_planner_controls(run, monkeypatch, setting, value):
    input_ref(run)
    save(run, "A task.", [])
    monkeypatch.setenv(setting, value)
    with pytest.raises(ToolError):
        restore(run.input_root, run.output, run.limits, "A task.")
