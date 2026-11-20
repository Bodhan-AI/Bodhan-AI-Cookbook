from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from time import monotonic

from dotenv import find_dotenv, load_dotenv

from bodhan_agent.budget import BudgetManager
from bodhan_agent.config import DEFAULT_DEADLINE_S, DEFAULT_MAX_COST_INR, DEFAULT_MAX_STEPS, LlmSettings, RunSettings
from bodhan_agent.llm import LlmClient
from bodhan_agent.loop import FINALIZE_RESERVE_S, Controller
from bodhan_agent.mcp_session import McpSession, McpUnavailable
from bodhan_agent.observe import Observer
from bodhan_agent.planner import Planner, PlannerError
from bodhan_agent.preflight import run_preflight
from bodhan_agent.report import Finalizer, inr
from bodhan_agent.schemas.ledger import Ledger
from bodhan_agent.schemas.loop import LoopResult
from bodhan_agent.schemas.planner import PlanOutcome
from bodhan_agent.schemas.preflight import PreflightResult
from bodhan_agent.schemas.report import RunReport, StopReason
from bodhan_agent.trace import TraceWriter

EXIT_CODES: dict[str, int] = {"completed": 0, "partial": 1, "failed": 2, "impossible": 2}
OWNED_FILES: tuple[str, ...] = ("trace.jsonl", "report.json", "result.md")
OWNED_DIRS: tuple[str, ...] = ("artifacts", "work")
SECRET_MARKERS: tuple[str, ...] = ("KEY", "TOKEN", "SECRET")


class SetupError(Exception):
    pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bodhan-agent",
        description="Run a task with Bodhan's Indian-language models through the bodhan_mcp tool server.",
    )
    parser.add_argument("--task", required=True, help="What to do, in any language.")
    parser.add_argument("--files", nargs="*", default=[], help="Input files, or one directory of inputs.")
    parser.add_argument("--out", required=True, help="Output folder for result.md, report.json and artifacts.")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="Most tool calls the agent may make.")
    parser.add_argument(
        "--max-cost-inr", type=float, default=DEFAULT_MAX_COST_INR, help="Bodhan spending limit in rupees."
    )
    parser.add_argument("--deadline-s", type=float, default=DEFAULT_DEADLINE_S, help="Wall-clock limit in seconds.")
    return parser.parse_args(argv)


def secrets_from(env: Mapping[str, str]) -> list[str]:
    return [value for name, value in env.items() if value and any(marker in name.upper() for marker in SECRET_MARKERS)]


def clean_outputs(settings: RunSettings) -> None:
    out = settings.out_dir.resolve()
    owned = [out / name for name in OWNED_DIRS]
    for path in settings.files:
        resolved = path.resolve()
        if any(resolved == folder or resolved.is_relative_to(folder) for folder in owned):
            raise SetupError(f"Input {path} is inside {resolved.parent}, which the agent clears; move it first.")
    if out.exists() and not out.is_dir():
        raise SetupError(f"--out {settings.out_dir} is a file, not a folder.")
    for name in OWNED_FILES:
        target = out / name
        if target.is_symlink() or target.is_file():
            target.unlink()
    for folder in owned:
        if folder.is_symlink():
            folder.unlink()
        elif folder.is_dir():
            shutil.rmtree(folder)
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)


class Run:
    def __init__(self, settings: RunSettings, env: Mapping[str, str] | None = None) -> None:
        self.settings = settings
        self.env = dict(os.environ if env is None else env)
        self.started_at = datetime.now(timezone.utc)
        self.started = monotonic()
        self.ledger = Ledger()
        self.budget = BudgetManager(settings.max_cost_inr)
        self.llm_settings = LlmSettings.from_env(self.env)
        self.trace: TraceWriter | None = None
        self.preflight: PreflightResult | None = None
        self.outcome: PlanOutcome | None = None
        self.loop: LoopResult | None = None
        self.notes: list[str] = []

    def deadline(self) -> float:
        return self.started + self.settings.deadline_s - FINALIZE_RESERVE_S

    async def finalize(self, stop_reason: StopReason | None = None, explanation: str | None = None) -> RunReport:
        parts = [part for part in (explanation, *self.notes) if part]
        finalizer = Finalizer(
            self.settings, self.ledger, self.budget, self.trace, self.started_at, monotonic() - self.started,
            preflight=self.preflight, outcome=self.outcome, loop=self.loop, stop_reason=stop_reason,
            explanation=" ".join(parts) or None,
        )  # fmt: skip
        return await finalizer.write()

    async def start_mcp(self) -> McpSession | None:
        session = McpSession(self.settings, env=self.env)
        try:
            return await session.start()
        except McpUnavailable as error:
            self.notes.append(f"The Bodhan tool server was not available: {error}")
            assert self.trace is not None
            await self.trace.emit("preflight", "stop", message="Bodhan MCP server unavailable; continuing without it.")
            return None

    async def execute(self) -> RunReport:
        clean_outputs(self.settings)
        secrets = secrets_from(self.env)
        self.trace = TraceWriter(self.settings.trace_path, secrets=secrets, cost_source=self.budget.cost_source)
        self.preflight = await run_preflight(self.settings, self.ledger)
        await self.trace.emit(
            "preflight", "preflight", message=f"{len(self.preflight.input_ids)} input(s) checked.",
            artifacts=[*self.preflight.input_ids, *self.preflight.literal_ids],
            result_summary={
                "inputs": self.preflight.input_ids, "literals": self.preflight.literal_ids,
                "needs_translation": self.preflight.needs_translation,
                "issues": [issue.code for issue in self.preflight.issues],
                "missing_tools": self.preflight.missing_tools,
            },
        )  # fmt: skip
        if not self.llm_settings.configured:
            missing = ", ".join(self.llm_settings.missing)
            return await self.finalize("llm_unavailable", f"The LLM is not configured: set {missing}.")
        llm = LlmClient(self.llm_settings)
        mcp = await self.start_mcp()
        try:
            return await self.plan_and_act(llm, mcp)
        finally:
            if mcp is not None:
                await mcp.aclose()
            await llm.aclose()

    async def plan_and_act(self, llm: LlmClient, mcp: McpSession | None) -> RunReport:
        assert self.trace is not None and self.preflight is not None
        planner = Planner(self.settings, self.ledger, llm, self.budget, self.trace, mcp=mcp, deadline=self.deadline())
        try:
            self.outcome = await planner.run(self.preflight)
        except PlannerError as error:
            return await self.finalize(error.stop_reason, error.message)
        if not self.outcome.gate.feasible or self.outcome.plan is None:
            return await self.finalize()
        controller = Controller(
            self.settings, self.ledger, self.outcome.plan, llm, self.budget, self.trace, Observer(self.ledger,
            secrets=secrets_from(self.env)), mcp=mcp, input_ids=self.preflight.input_ids,
            literal_ids=self.preflight.literal_ids, started=self.started,
        )  # fmt: skip
        self.loop = await controller.run()
        return await self.finalize()

    async def run(self) -> RunReport:
        try:
            return await self.execute()
        except SetupError:
            raise
        except Exception as error:
            if self.trace is None:
                self.settings.work_dir.mkdir(parents=True, exist_ok=True)
                self.trace = TraceWriter(self.settings.trace_path, secrets=secrets_from(self.env))
            return await self.finalize("internal_error", f"Unexpected error: {type(error).__name__}: {error}")


def summary(report: RunReport, settings: RunSettings) -> str:
    lines = [f"status: {report.status} ({report.stop_reason})"]
    for item in report.deliverables:
        lines.append(
            f"  {item.id} {item.kind}: {item.status}" + (f" -> {settings.out_dir / item.path}" if item.path else "")
        )
    lines.append(f"cost: {inr(report.cost.bodhan_inr)} of {inr(settings.max_cost_inr)}")
    lines.append(f"result: {settings.result_path}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(find_dotenv(usecwd=True))
    try:
        settings = RunSettings.create(
            args.task, args.files, args.out, max_steps=args.max_steps, max_cost_inr=args.max_cost_inr,
            deadline_s=args.deadline_s,
        )  # fmt: skip
        report = asyncio.run(Run(settings).run())
    except (SetupError, ValueError) as error:
        print(f"bodhan-agent: {error}", file=sys.stderr)
        return 2
    print(summary(report, settings))
    return EXIT_CODES[report.status]


if __name__ == "__main__":
    sys.exit(main())
