from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import bodhan_agent.__main__ as cli
from bodhan_agent.clients.mcp_session import McpUnavailable
from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.llm import LlmReply, ToolCall
from bodhan_agent.schemas.mcp import McpResult, McpToolSpec
from bodhan_agent.schemas.report import RunReport
from bodhan_agent.schemas.trace import LlmTokens

TAMIL = "தாவரங்கள் சூரிய ஒளியில் உணவு தயாரிக்கின்றன."
ENV = {"LLM_BASE_URL": "https://llm.test/v1", "LLM_MODEL": "m", "LLM_API_KEY": "sk-test-secret-123456",
       "BODHAN_API_KEY": "bodhan-secret-abcdef"}  # fmt: skip
USAGE = {"model_calls": 1, "http_attempts": 1, "estimated_cost_inr": 0.001, "billing_uncertain": False,
         "cache_hits": 0, "possibly_billed_failed_attempts": 0, "latency_ms": 1}  # fmt: skip
PLAN = {
    "task_en": "Translate the notes to Tamil.",
    "goal": "Tamil notes and a summary.",
    "deliverables": [
        {
            "id": "D1",
            "kind": "markdown",
            "language": "ta",
            "produced_by": "translate_text",
            "description": "Tamil notes",
        },
        {"id": "D2", "kind": "answer", "language": "en", "description": "Summary"},
    ],
    "steps": [{"id": "S1", "intent": "Translate", "why": "Needed for the task.", "tool": "translate_text"}],
    "feasibility": {"status": "feasible"},
}


class FakeLlm:
    script: list[Any] = []
    seen: list[list[dict[str, Any]]] = []
    closed = 0

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.turn = 0

    async def complete(self, messages: Any, tools: Any = None, *, deadline: float | None = None) -> LlmReply:
        FakeLlm.seen.append(list(messages))
        self.turn += 1
        step = FakeLlm.script.pop(0) if FakeLlm.script else "Nothing more to do."
        if callable(step):
            step = step(messages)
        if isinstance(step, str):
            return LlmReply(content=step, latency_ms=1, attempts=1, finish_reason="stop",
                            tokens=LlmTokens(prompt=10, completion=5))  # fmt: skip
        calls = [ToolCall(id=f"c{self.turn}{index}", name=name, raw_arguments=json.dumps(args), arguments=args)
                 for index, (name, args) in enumerate(step)]  # fmt: skip
        return LlmReply(tool_calls=calls, latency_ms=1, attempts=1, finish_reason="tool_calls",
                        tokens=LlmTokens(prompt=10, completion=5))  # fmt: skip

    async def aclose(self) -> None:
        FakeLlm.closed += 1


class FakeMcp:
    fail_start = False
    started = 0
    closed = 0
    calls: list[tuple[str, dict[str, Any]]] = []
    env: dict[str, str] = {}

    def __init__(self, settings: RunSettings, env: Any = None) -> None:
        self.settings = settings
        FakeMcp.env = dict(env or {})
        self.tools = [McpToolSpec(name="translate_text", description="Translate.",
                                  input_schema={"type": "object", "properties": {}})]  # fmt: skip

    async def start(self) -> FakeMcp:
        if FakeMcp.fail_start:
            raise McpUnavailable("server crashed")
        FakeMcp.started += 1
        return self

    async def call(self, name: str, args: dict[str, Any], *, deadline: float | None = None) -> McpResult:
        FakeMcp.calls.append((name, args))
        out = self.settings.work_dir / "notes.ta-1.md"
        out.write_text(TAMIL, encoding="utf-8")
        data = {"output_path": str(out), "target_language": "ta", "script": "native", "translation": TAMIL,
                "translation_is_preview": False, "translated_chars": len(TAMIL), "verification_status": "passed",
                "usage": USAGE}  # fmt: skip
        return McpResult(tool=name, ok=True, latency_ms=1, data=data)

    async def aclose(self) -> None:
        FakeMcp.closed += 1


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeLlm.script, FakeLlm.seen, FakeLlm.closed = [], [], 0
    FakeMcp.fail_start, FakeMcp.started, FakeMcp.closed, FakeMcp.calls, FakeMcp.env = False, 0, 0, [], {}
    monkeypatch.setattr(cli, "LlmClient", FakeLlm)
    monkeypatch.setattr(cli, "McpSession", FakeMcp)


@pytest.fixture
def inputs(tmp_path: Path) -> Path:
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "notes.md").write_text("Plants make food from sunlight.", encoding="utf-8")
    return folder / "notes.md"


def settings_for(tmp_path: Path, files: list[Path], task: str = "Translate my notes to Tamil.") -> RunSettings:
    return RunSettings.create(task, [str(path) for path in files], str(tmp_path / "out"))


def happy_script(settings: RunSettings, notes: Path) -> list[Any]:
    def finish(messages: Any) -> list[tuple[str, dict[str, Any]]]:
        path = str(settings.work_dir / "notes.ta-1.md")
        return [("finish", {"reason": "done", "answer_en": "The notes explain photosynthesis.",
                            "deliverables": [{"id": "D1", "path": path}, {"id": "D2"}]})]  # fmt: skip

    return [
        json.dumps(PLAN),
        [("translate_text", {"reason": "translate", "text_path": str(notes), "target_language": "ta"})],
        finish,
    ]


def events(settings: RunSettings) -> list[dict[str, Any]]:
    return [json.loads(line) for line in settings.trace_path.read_text(encoding="utf-8").splitlines()]


def test_parse_args_defaults() -> None:
    args = cli.parse_args(["--task", "x", "--out", "o"])
    assert (args.task, args.files, args.out, args.max_steps, args.max_cost_inr, args.deadline_s) == (
        "x", [], "o", 30, 20.0, 290.0,
    )  # fmt: skip


def test_parse_args_values() -> None:
    args = cli.parse_args(["--task", "x", "--files", "a", "b", "--out", "o", "--max-steps", "5",
                           "--max-cost-inr", "1.5", "--deadline-s", "60"])  # fmt: skip
    assert (args.files, args.max_steps, args.max_cost_inr, args.deadline_s) == (["a", "b"], 5, 1.5, 60.0)


def test_parse_args_requires_task_and_out() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--out", "o"])
    with pytest.raises(SystemExit):
        cli.parse_args(["--task", "x"])


def test_secrets_from_env() -> None:
    env = {"LLM_API_KEY": "a", "BODHAN_ASR_KEY": "b", "GITHUB_TOKEN": "c", "MY_SECRET": "d", "PATH": "/bin",
           "EMPTY_KEY": ""}  # fmt: skip
    assert sorted(cli.secrets_from(env)) == ["a", "b", "c", "d"]


def test_clean_outputs_removes_only_owned(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    out = settings.out_dir
    (out / "work" / "deep").mkdir(parents=True)
    (out / "work" / "deep" / "old.txt").write_text("x")
    (out / "artifacts").mkdir()
    (out / "artifacts" / "old.mp3").write_text("x")
    for name in ("trace.jsonl", "report.json", "result.md", "keep.txt"):
        (out / name).write_text("x")
    (out / "notes").mkdir()
    cli.clean_outputs(settings)
    assert sorted(path.name for path in out.iterdir()) == ["artifacts", "keep.txt", "notes", "work"]
    assert list(settings.work_dir.iterdir()) == [] and list(settings.artifacts_dir.iterdir()) == []


def test_clean_outputs_unlinks_symlinked_dirs(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.txt").write_text("keep")
    settings.out_dir.mkdir()
    (settings.out_dir / "work").symlink_to(elsewhere, target_is_directory=True)
    cli.clean_outputs(settings)
    assert (elsewhere / "precious.txt").read_text() == "keep"
    assert settings.work_dir.is_dir() and not settings.work_dir.is_symlink()


def test_clean_outputs_refuses_inputs_inside_owned_dirs(tmp_path: Path) -> None:
    trapped = tmp_path / "out" / "work" / "input.md"
    trapped.parent.mkdir(parents=True)
    trapped.write_text("mine")
    with pytest.raises(cli.SetupError):
        cli.clean_outputs(settings_for(tmp_path, [trapped]))
    assert trapped.read_text() == "mine"


def test_clean_outputs_refuses_file_out(tmp_path: Path, inputs: Path) -> None:
    (tmp_path / "out").write_text("a file")
    with pytest.raises(cli.SetupError):
        cli.clean_outputs(settings_for(tmp_path, [inputs]))


async def test_full_run_completes(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    FakeLlm.script = happy_script(settings, inputs)
    report = await cli.Run(settings, env=ENV).run()
    assert (report.status, report.stop_reason) == ("completed", "finished")
    assert [(item.id, item.status, item.path) for item in report.deliverables] == [
        ("D1", "verified", "artifacts/notes.ta-1.md"), ("D2", "verified", None),
    ]  # fmt: skip
    assert (settings.artifacts_dir / "notes.ta-1.md").read_text(encoding="utf-8") == TAMIL
    assert settings.report_path.exists() and settings.result_path.exists()
    assert report.cost.llm_tokens == LlmTokens(prompt=30, completion=15)
    assert (FakeMcp.started, FakeMcp.closed, FakeLlm.closed) == (1, 1, 1)
    kinds = [event["kind"] for event in events(settings)]
    assert kinds[:3] == ["preflight", "plan", "gate"] and kinds[-1] == "stop"
    assert [event["phase"] for event in events(settings)][-1] == "finalize"


async def test_secrets_never_reach_trace(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs], task="Translate my notes. My key is sk-test-secret-123456.")
    FakeLlm.script = happy_script(settings, inputs)
    await cli.Run(settings, env=ENV).run()
    text = settings.trace_path.read_text(encoding="utf-8")
    assert "sk-test-secret-123456" not in text and "bodhan-secret-abcdef" not in text


async def test_server_env_hides_llm_key(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    FakeLlm.script = happy_script(settings, inputs)
    await cli.Run(settings, env=ENV).run()
    assert FakeMcp.env["BODHAN_API_KEY"] == "bodhan-secret-abcdef"
    assert settings.server_env(FakeMcp.env)["LLM_API_KEY"] == ""


async def test_unconfigured_llm(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    report = await cli.Run(settings, env={"BODHAN_API_KEY": "x"}).run()
    assert (report.status, report.stop_reason) == ("failed", "llm_unavailable")
    assert "LLM_BASE_URL" in (report.explanation or "")
    assert FakeMcp.started == 0 and FakeLlm.seen == []
    assert [event["kind"] for event in events(settings)] == ["preflight", "stop"]


async def test_planner_error_is_reported(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    FakeLlm.script = ["not json", "still not json"]
    report = await cli.Run(settings, env=ENV).run()
    assert (report.status, report.stop_reason) == ("failed", "planner_error")
    assert FakeMcp.closed == 1 and FakeLlm.closed == 1


async def test_gate_refusal_skips_loop(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    plan = dict(PLAN, deliverables=[{"id": "D1", "kind": "audio", "language": "fr", "description": "French"}])
    FakeLlm.script = [json.dumps(plan)]
    report = await cli.Run(settings, env=ENV).run()
    assert (report.status, report.stop_reason) == ("impossible", "unsupported_language")
    assert len(FakeLlm.seen) == 1


async def test_mcp_failure_continues_without_bodhan(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    FakeMcp.fail_start = True
    FakeLlm.script = [json.dumps(PLAN), "I cannot translate without Bodhan.", "Nothing was translated."]
    report = await cli.Run(settings, env=ENV).run()
    assert report.status == "partial"
    assert "Bodhan tool server was not available: server crashed" in (report.explanation or "")
    assert FakeMcp.calls == [] and FakeMcp.closed == 0


async def test_unexpected_error_still_writes_report(
    tmp_path: Path, inputs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path, [inputs])

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(cli, "run_preflight", boom)
    report = await cli.Run(settings, env=ENV).run()
    assert (report.status, report.stop_reason) == ("failed", "internal_error")
    assert report.explanation == "Unexpected error: RuntimeError: disk on fire"
    assert json.loads(settings.report_path.read_text())["stop_reason"] == "internal_error"


async def test_rerun_replaces_previous_outputs(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    FakeLlm.script = happy_script(settings, inputs)
    await cli.Run(settings, env=ENV).run()
    (settings.work_dir / "stale.txt").write_text("old")
    FakeLlm.script = happy_script(settings, inputs)
    report = await cli.Run(settings, env=ENV).run()
    assert report.status == "completed"
    assert not (settings.work_dir / "stale.txt").exists()
    assert sorted(path.name for path in settings.artifacts_dir.iterdir()) == ["notes.ta-1.md"]


def test_summary_text(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    FakeLlm.script = happy_script(settings, inputs)
    report: RunReport = asyncio.run(cli.Run(settings, env=ENV).run())
    lines = cli.summary(report, settings).splitlines()
    assert lines[0] == "status: completed (finished)"
    assert lines[1] == f"  D1 markdown: verified -> {settings.out_dir / 'artifacts/notes.ta-1.md'}"
    assert lines[2] == "  D2 answer: verified"
    assert lines[3] == "cost: Rs 0.0010 of Rs 20.00"
    assert lines[4] == f"result: {settings.result_path}"


@pytest.mark.parametrize(("script", "code"), [("happy", 0), ("partial", 1), ("bad_plan", 2)])
def test_main_exit_codes(
    tmp_path: Path, inputs: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    script: str, code: int,
) -> None:  # fmt: skip
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    settings = settings_for(tmp_path, [inputs])
    FakeLlm.script = {
        "happy": happy_script(settings, inputs),
        "partial": [json.dumps(PLAN), "Done.", "The notes were not translated."],
        "bad_plan": ["no", "no"],
    }[script]
    result = cli.main(
        ["--task", "Translate my notes to Tamil.", "--files", str(inputs), "--out", str(settings.out_dir)]
    )
    assert result == code
    assert capsys.readouterr().out.startswith("status: ")


def test_main_setup_error_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trapped = tmp_path / "out" / "work" / "input.md"
    trapped.parent.mkdir(parents=True)
    trapped.write_text("mine")
    assert cli.main(["--task", "x", "--files", str(trapped), "--out", str(tmp_path / "out")]) == 2
    assert "which the agent clears" in capsys.readouterr().err


def test_main_invalid_settings_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--task", "x", "--out", str(tmp_path / "out"), "--max-steps", "0"]) == 2
    assert "Invalid run settings" in capsys.readouterr().err


def test_main_loads_dotenv(tmp_path: Path, inputs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("\n".join(f"{name}={value}" for name, value in ENV.items()), encoding="utf-8")
    monkeypatch.chdir(project)
    settings = settings_for(tmp_path, [inputs])
    FakeLlm.script = happy_script(settings, inputs)
    result = cli.main(
        ["--task", "Translate my notes to Tamil.", "--files", str(inputs), "--out", str(settings.out_dir)]
    )
    assert result == 0
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


async def test_full_run_writes_plan_files(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    FakeLlm.script = happy_script(settings, inputs)
    await cli.Run(settings, env=ENV).run()
    plan = json.loads((settings.out_dir / "plan.json").read_text())
    assert plan["finished"] is True
    assert [(step["id"], step["status"]) for step in plan["steps"]] == [("S1", "done")]
    assert plan["estimate"]["budget_inr"] == 20.0
    assert (settings.out_dir / "plan.md").read_text(encoding="utf-8").startswith("# Plan (final)")
    assert "## Plan" in settings.result_path.read_text(encoding="utf-8")


async def test_gate_refusal_still_writes_plan(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    plan = dict(PLAN, deliverables=[{"id": "D1", "kind": "audio", "language": "fr", "description": "French"}])
    FakeLlm.script = [json.dumps(plan)]
    await cli.Run(settings, env=ENV).run()
    saved = json.loads((settings.out_dir / "plan.json").read_text())
    assert saved["finished"] is True and saved["steps"][0]["status"] == "skipped"


def test_clean_outputs_removes_plan_files(tmp_path: Path, inputs: Path) -> None:
    settings = settings_for(tmp_path, [inputs])
    settings.out_dir.mkdir()
    for name in ("plan.md", "plan.json"):
        (settings.out_dir / name).write_text("old")
    cli.clean_outputs(settings)
    assert not (settings.out_dir / "plan.md").exists() and not (settings.out_dir / "plan.json").exists()
