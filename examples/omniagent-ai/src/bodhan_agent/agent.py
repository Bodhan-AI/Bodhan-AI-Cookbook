import asyncio
import json
import os
import sys
import time
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

from .budget import ExecutionBudget, BudgetExceededError
from .cost import CostTracker
from .files import InputFileManager
from .reporting import ReportGenerator
from .retry import retry_async, TransientMCPError
from .tracing import TraceLogger
from .transcription_mcp import MCPChunkedTranscription
from .planner_retry import create_plan_with_retry

load_dotenv()


class OmniAgent:
    """Gemini-planned agent using Bodhan MCP tools."""

    def __init__(self, input_dir=None, output_dir=None):
        self.project_root = Path(__file__).resolve().parents[2]
        self.input_dir = self._resolve_directory(input_dir or "data")
        self.output_dir = self._resolve_directory(output_dir or "out")
        self.file_manager = InputFileManager(self.input_dir)
        self.tools = []

        self.llm = OpenAI(
            api_key=os.environ["LLM_API_KEY"],
            base_url=os.environ["LLM_BASE_URL"],
            timeout=60.0,
        )
        self.model = os.environ["LLM_MODEL"]
        self.tracer = TraceLogger(output_dir=self.output_dir)
        self.reporter = ReportGenerator(output_dir=self.output_dir)
        self.cost_tracker = CostTracker()

    def _resolve_directory(self, directory):
        path = Path(directory)
        if not path.is_absolute():
            path = self.project_root / path
        return path.resolve()

    def server_parameters(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.project_root / "src")
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "bodhan_mcp"],
            cwd=str(self.project_root),
            env=env,
        )

    async def discover_tools(self, session):
        result = await session.list_tools()
        self.tools = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema,
            }
            for tool in result.tools
            if tool.name != "server_status"
        ]
        return self.tools

    def plan_next_step(self, task, history):
        prompt = {
            "task": task,
            "available_tools": self.tools,
            "available_files": self.file_manager.discover_files(),
            "previous_steps": history,
        }

        def on_planner_retry(attempt, error_type, delay_seconds):
            self.tracer.log(
                "planner_retry",
                failed_attempt=attempt,
                error_type=error_type,
                delay_seconds=delay_seconds,
            )
            print(
                f"Gemini planner temporarily unavailable "
                f"({error_type}). Retrying in {delay_seconds}s..."
            )

        response = create_plan_with_retry(
            self.llm.chat.completions.create,
            on_retry=on_planner_retry,
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the planner for OmniAgent AI. "
                        "Complete the task using available MCP tools. "
                        "Choose exactly one action per response. "
                        "Use actual outputs from previous tools. "
                        "Never invent extracted or translated text. "
                        "Use only files listed in available_files. "
                        "Never invent file paths. "
                        "Return JSON containing: "
                        "'action', 'tool', 'arguments', 'reason'. "
                        "Use action='call_tool' to execute a tool. "
                        "Use action='finish', tool=null, "
                        "arguments={} when the task is complete. "
                        "All planning must be in English. "
                        "All Indian-language processing must be "
                        "performed exclusively by Bodhan tools."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        prompt,
                        ensure_ascii=False,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=1000,
        )

        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("Gemini returned an empty plan.")

        plan = json.loads(content)
        if not isinstance(plan, dict):
            raise ValueError("Gemini plan must be a JSON object.")
        return plan

    def validate_plan(self, plan):
        action = plan.get("action")

        if action == "finish":
            if (
                plan.get("tool") is not None
                or plan.get("arguments") != {}
            ):
                raise ValueError("Invalid finish action.")
            return

        if action != "call_tool":
            raise ValueError(f"Invalid action: {action}")

        tool_lookup = {
            tool["name"]: tool
            for tool in self.tools
        }

        name = plan.get("tool")
        arguments = plan.get("arguments")

        if name not in tool_lookup:
            raise ValueError(f"Unknown tool: {name}")

        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object.")

        schema = tool_lookup[name]["input_schema"]
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        unknown = set(arguments) - set(properties)
        missing = set(required) - set(arguments)

        if unknown:
            raise ValueError(
                f"Unknown arguments: {sorted(unknown)}"
            )
        if missing:
            raise ValueError(
                f"Missing arguments: {sorted(missing)}"
            )

        file_arguments = {
            "extract_text": "file_path",
            "transcribe_audio": "audio_path",
        }

        if name in file_arguments:
            key = file_arguments[name]
            requested = arguments.get(key)

            if not isinstance(requested, str):
                raise ValueError(f"{key} must be a string.")

            path = Path(requested)
            if not path.is_absolute():
                project_relative = self.project_root / path
                if project_relative.is_file():
                    path = project_relative
                else:
                    path = self.input_dir / path

            validated = self.file_manager.validate_file(path)
            arguments[key] = validated

    @staticmethod
    def is_transient_error(message):
        message = str(message).lower()
        markers = (
            "429",
            "rate limit",
            "rate_limit",
            "too many requests",
            "500 internal server",
            "502",
            "503",
            "504",
            "service unavailable",
            "gateway timeout",
            "connection timeout",
            "connection timed out",
            "read timeout",
        )
        return any(marker in message for marker in markers)

    async def execute_tool(self, session, plan):
        result = await session.call_tool(
            plan["tool"],
            arguments=plan["arguments"],
        )

        if result.isError:
            message = str(result.content)
            if self.is_transient_error(message):
                raise TransientMCPError(message)
            raise RuntimeError(f"MCP tool failed: {message}")

        if result.structuredContent is not None:
            data = result.structuredContent
            if (
                isinstance(data, dict)
                and isinstance(data.get("result"), dict)
            ):
                data = data["result"]
        else:
            items = [
                item.text
                for item in result.content
                if hasattr(item, "text")
            ]
            if not items:
                raise RuntimeError("Empty MCP response.")
            data = json.loads(items[0])

        if not isinstance(data, dict):
            raise RuntimeError("Unexpected MCP response format.")

        if data.get("status") == "error":
            message = str(
                data.get("message", "Unknown tool error")
            )
            if self.is_transient_error(message):
                raise TransientMCPError(message)
            raise RuntimeError(f"Tool error: {message}")

        return data

    async def execute_with_retry(
        self,
        session,
        plan,
        budget,
        step,
        max_attempts=3,
    ):
        tool_name = plan["tool"]
        attempts = 0

        async def operation():
            nonlocal attempts

            try:
                budget.reserve_tool_call()
            except BudgetExceededError:
                self.tracer.log(
                    "budget_exceeded",
                    step=step,
                    tool=tool_name,
                    budget=budget.report(),
                )
                raise

            attempts += 1

            self.tracer.log(
                "budget_reserved",
                step=step,
                tool=tool_name,
                attempt=attempts,
                budget=budget.report(),
            )

            self.cost_tracker.record_call(tool_name)

            self.tracer.log(
                "cost_recorded",
                step=step,
                tool=tool_name,
                attempt=attempts,
                cost=self.cost_tracker.report(),
            )

            self.tracer.log(
                "tool_call",
                step=step,
                tool=tool_name,
                attempt=attempts,
                arguments=plan["arguments"],
            )

            print(
                f"Calling {tool_name} "
                f"(attempt {attempts}/{max_attempts})"
            )
            print("Remaining tool calls:", budget.remaining)

            start = time.perf_counter()

            try:
                result = await self.execute_tool(session, plan)
            except Exception as exc:
                self.tracer.log(
                    "tool_error",
                    step=step,
                    tool=tool_name,
                    attempt=attempts,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise

            duration_ms = round(
                (time.perf_counter() - start) * 1000,
                2,
            )

            self.tracer.log(
                "tool_result",
                step=step,
                tool=tool_name,
                attempt=attempts,
                status=result.get("status", "unknown"),
                duration_ms=duration_ms,
                result=result,
            )
            return result

        def on_retry(attempt, error_type, delay_seconds):
            self.tracer.log(
                "tool_retry",
                step=step,
                tool=tool_name,
                failed_attempt=attempt,
                error_type=error_type,
                delay_seconds=delay_seconds,
                remaining_budget=budget.remaining,
            )
            print(
                f"Temporary failure. "
                f"Retrying in {delay_seconds}s..."
            )

        return await retry_async(
            operation=operation,
            max_attempts=max_attempts,
            initial_delay=1.0,
            on_retry=on_retry,
        )

    async def execute_planned_action(
        self,
        session,
        plan,
        budget,
        step,
        max_attempts=3,
    ):
        """Route WAV transcription through budget-aware chunking."""

        if plan["tool"] == "transcribe_audio":
            audio_path = Path(plan["arguments"]["audio_path"])

            if audio_path.suffix.lower() == ".wav":
                adapter = MCPChunkedTranscription(
                    agent=self,
                    chunk_seconds=30,
                )

                with tempfile.TemporaryDirectory(
                    prefix="omniagent_wav_chunks_"
                ) as temp_dir:
                    return await adapter.transcribe(
                        session=session,
                        audio_path=audio_path,
                        language=plan["arguments"].get(
                            "language", "hi"
                        ),
                        output_dir=temp_dir,
                        budget=budget,
                        step=step,
                        max_attempts=max_attempts,
                    )

        return await self.execute_with_retry(
            session=session,
            plan=plan,
            budget=budget,
            step=step,
            max_attempts=max_attempts,
        )

    async def run(
        self,
        task,
        max_steps=4,
        max_tool_calls=2,
        max_attempts=3,
    ):
        if (
            not isinstance(max_steps, int)
            or isinstance(max_steps, bool)
            or max_steps <= 0
        ):
            raise ValueError(
                "max_steps must be a positive integer."
            )

        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or max_attempts <= 0
        ):
            raise ValueError(
                "max_attempts must be a positive integer."
            )

        budget = ExecutionBudget(
            max_tool_calls=max_tool_calls
        )
        self.cost_tracker = CostTracker()

        history = []
        start_time = time.perf_counter()
        outcome = "incomplete"
        error_details = None
        report_paths = {}

        self.tracer.log(
            "agent_start",
            task=task,
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
            max_attempts=max_attempts,
        )

        try:
            print("Starting OmniAgent AI...")
            print(f"Task: {task}")
            print("Input directory:", self.input_dir)
            print("Output directory:", self.output_dir)
            print(
                "Discovered files:",
                len(self.file_manager.discover_files()),
            )
            print(
                f"Tool-call budget: {budget.max_tool_calls}"
            )
            print(
                f"Maximum attempts per tool: "
                f"{max_attempts}\n"
            )

            async with stdio_client(
                self.server_parameters()
            ) as (read, write):

                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await self.discover_tools(session)

                    print(
                        f"Connected to {len(self.tools)} "
                        "Bodhan MCP tools.\n"
                    )

                    self.tracer.log(
                        "mcp_connected",
                        tool_count=len(self.tools),
                        tools=[
                            tool["name"]
                            for tool in self.tools
                        ],
                    )

                    for step in range(1, max_steps + 1):
                        print(f"--- Agent Step {step} ---")

                        plan = self.plan_next_step(
                            task, history
                        )
                        self.validate_plan(plan)

                        self.tracer.log(
                            "plan",
                            step=step,
                            action=plan["action"],
                            tool=plan.get("tool"),
                            reason=plan.get("reason", ""),
                        )

                        if plan["action"] == "finish":
                            outcome = "success"
                            print(
                                "Agent finished the task.\n"
                            )
                            break

                        tool_name = plan["tool"]
                        print("Selected tool:", tool_name)
                        print(
                            "Arguments:",
                            json.dumps(
                                plan["arguments"],
                                ensure_ascii=False,
                            ),
                        )

                        result = await self.execute_planned_action(
                            session=session,
                            plan=plan,
                            budget=budget,
                            step=step,
                            max_attempts=max_attempts,
                        )

                        print(
                            "Result:",
                            json.dumps(
                                result,
                                ensure_ascii=False,
                                indent=2,
                            ),
                        )

                        history.append({
                            "tool": tool_name,
                            "arguments": plan["arguments"],
                            "result": result,
                        })
                        print()

        except BudgetExceededError as exc:
            outcome = "budget_exceeded"
            error_details = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            self.tracer.log(
                "agent_error",
                **error_details,
            )
            raise

        except Exception as exc:
            outcome = "error"
            error_details = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            self.tracer.log(
                "agent_error",
                **error_details,
            )
            raise

        finally:
            duration_ms = round(
                (time.perf_counter() - start_time) * 1000,
                2,
            )

            self.tracer.log(
                "agent_finish",
                status=outcome,
                executed_steps=len(history),
                budget=budget.report(),
                cost=self.cost_tracker.report(),
                duration_ms=duration_ms,
            )

            run_result = {
                "status": outcome,
                "history": history,
                "budget": budget.report(),
                "cost": self.cost_tracker.report(),
                "trace_path": str(self.tracer.trace_path),
            }

            if error_details is not None:
                run_result["error"] = error_details

            try:
                report_paths = self.reporter.generate(
                    task=task,
                    run_result=run_result,
                )
                print(
                    "\nReport generated:",
                    report_paths["report_path"],
                )
                print(
                    "Markdown result generated:",
                    report_paths["result_path"],
                )
            except Exception as report_exc:
                self.tracer.log(
                    "report_error",
                    error_type=type(report_exc).__name__,
                    error=str(report_exc),
                )
                print(
                    "WARNING: Report generation failed:",
                    report_exc,
                )

            print(
                "\nExecution trace:",
                self.tracer.trace_path,
            )
            print("Execution status:", outcome)
            print("Final budget:", budget.report())
            print(
                "Cost report:",
                json.dumps(
                    self.cost_tracker.report(),
                    ensure_ascii=False,
                    indent=2,
                ),
            )

        return {
            **run_result,
            **report_paths,
        }


async def main():
    task = (
        "Extract Hindi text from data/sample_ocr.png "
        "and translate it into English."
    )

    agent = OmniAgent(
        input_dir="data",
        output_dir="out",
    )

    result = await agent.run(
        task=task,
        max_steps=4,
        max_tool_calls=2,
        max_attempts=3,
    )

    executed_tools = [
        item["tool"]
        for item in result["history"]
    ]

    assert result["status"] == "success"
    assert executed_tools == [
        "extract_text",
        "translate_text",
    ]

    translated = result["history"][-1]["result"].get(
        "translated_text", ""
    )

    assert translated.strip()
    assert result["budget"]["tool_calls_used"] == 2
    assert result["cost"]["total_calls"] == 2
    assert result["cost"]["total_cost_inr"] is None

    assert Path(result["report_path"]).exists()
    assert Path(result["result_path"]).exists()

    print(
        "\nPASS: Multi-step OCR → Translation workflow!"
    )
    print("PASS: File discovery integrated!")
    print("PASS: Input file validation integrated!")
    print("PASS: Execution budget enforced!")
    print("PASS: Retry and cost tracking integrated!")
    print("PASS: Reports generated!")


if __name__ == "__main__":
    asyncio.run(main())