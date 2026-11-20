import asyncio
import time

from bodhan_agent.verification import verify_translation
from bodhan_agent.argument_builder import prepare_tool_arguments
from bodhan_agent.result_formatter import format_tool_result
from bodhan_agent.budget import CostBudget, BudgetExceeded


DEFAULT_MAX_STEPS = 30
DEFAULT_TOOL_TIMEOUT = 60


class ExecutionError(RuntimeError):
    """Execution failure that preserves completed and failed steps."""

    def __init__(self, message, results, budget_summary=None):
        super().__init__(message)
        self.results = results
        self.budget_summary = budget_summary or {}


async def execute_plan(
    session,
    plan,
    files,
    task,
    max_steps=DEFAULT_MAX_STEPS,
    tool_timeout=DEFAULT_TOOL_TIMEOUT,
    max_cost_inr=20.0,
    artifacts_dir=None,
):
    results = []
    steps = plan.get("steps", [])

    budget = CostBudget(max_cost_inr=max_cost_inr)

    if len(steps) > max_steps:
        raise ExecutionError(
            f"Plan contains {len(steps)} steps, exceeding max_steps={max_steps}.",
            results,
            budget.summary(),
        )

    try:
        available_tools_response = await session.list_tools()
        available_tools = available_tools_response.tools

        tool_lookup = {
            tool.name: tool
            for tool in available_tools
        }

        for step_number, step in enumerate(steps, start=1):
            tool_name = step.get("tool", "unknown")
            description = step.get("description", "")

            arguments = {}
            estimated_cost = 0.0
            start_time = time.perf_counter()
            failure_recorded = False

            print(
                f"\nStep {step_number}/{len(steps)}: "
                f"{description}"
            )
            print(f"Tool: {tool_name}")

            try:
                if tool_name not in tool_lookup:
                    raise ValueError(
                        f"Unknown MCP tool: {tool_name}"
                    )

                selected_tool = tool_lookup[tool_name]

                # Check budget before preparing/executing the call.
                estimated_cost = budget.check(tool_name)

                arguments = prepare_tool_arguments(
                    task=task,
                    step=step,
                    files=files,
                    previous_results=results,
                    tool_schema=selected_tool.inputSchema,
                    artifacts_dir=artifacts_dir,
                )

                print(f"Arguments: {arguments}")

                # Record estimated cost for the attempted API call.
                budget.record(
                    tool_name,
                    estimated_cost,
                )

                start_time = time.perf_counter()

                tool_result = await asyncio.wait_for(
                    session.call_tool(
                        tool_name,
                        arguments=arguments,
                    ),
                    timeout=tool_timeout,
                )

                latency = round(
                    time.perf_counter() - start_time,
                    3,
                )

                if tool_result.isError:
                    error_details = str(tool_result.content)

                    results.append({
                        "step": description,
                        "tool": tool_name,
                        "arguments": arguments,
                        "result": None,
                        "verification": None,
                        "latency_seconds": latency,
                        "estimated_cost_inr": estimated_cost,
                        "status": "failed",
                        "error": error_details,
                    })

                    failure_recorded = True

                    raise RuntimeError(
                        f"Tool {tool_name} returned an error: "
                        f"{error_details}"
                    )

                # Format the successful MCP response.
                formatted_result = format_tool_result(
                    tool_name,
                    tool_result,
                )

                verification = None

                # Verify translation coverage only.
                if tool_name == "indic_translate":
                    try:
                        verification = verify_translation(
                            source_text=arguments.get(
                                "text", ""
                            ),
                            translated_text=formatted_result.get(
                                "text", ""
                            ),
                        )

                    except Exception as verification_error:
                        # Translation tool succeeded.
                        # Verification failure should not erase
                        # the successful translation result.
                        verification = {
                            "status": "warning",
                            "verified": False,
                            "reason": (
                                "Verification could not be completed: "
                                f"{verification_error}"
                            ),
                        }

                        print(
                            "Verification warning: "
                            f"{verification_error}"
                        )

                # Save successful tool execution.
                results.append({
                    "step": description,
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": formatted_result,
                    "verification": verification,
                    "latency_seconds": latency,
                    "estimated_cost_inr": estimated_cost,
                    "status": "success",
                    "error": None,
                })

                print("Status: SUCCESS")
                print(f"Latency: {latency} seconds")
                print(
                    f"Estimated call cost: "
                    f"₹{estimated_cost:.2f}"
                )

                if verification:
                    print(
                        "Translation verification: "
                        f"{verification.get('status', 'unknown')}"
                    )

            except asyncio.TimeoutError:
                latency = round(
                    time.perf_counter() - start_time,
                    3,
                )

                results.append({
                    "step": description,
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": None,
                    "verification": None,
                    "latency_seconds": latency,
                    "estimated_cost_inr": estimated_cost,
                    "status": "failed",
                    "error": (
                        f"Tool timed out after "
                        f"{tool_timeout} seconds."
                    ),
                })

                failure_recorded = True

                raise RuntimeError(
                    f"Tool {tool_name} timed out after "
                    f"{tool_timeout} seconds."
                )

            except Exception as exc:
                # Do not add a duplicate failure record.
                if not failure_recorded:
                    latency = round(
                        time.perf_counter() - start_time,
                        3,
                    )

                    results.append({
                        "step": description,
                        "tool": tool_name,
                        "arguments": arguments,
                        "result": None,
                        "verification": None,
                        "latency_seconds": latency,
                        "estimated_cost_inr": estimated_cost,
                        "status": "failed",
                        "error": str(exc),
                    })

                raise RuntimeError(
                    f"Step {step_number} failed: {exc}"
                ) from exc

        return results

    except Exception as exc:
        raise ExecutionError(
            str(exc),
            results,
            budget.summary(),
        ) from exc


def aggregate_usage(results):
    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "usage_available": False,
        "actual_cost_inr": None,
        "estimated_cost_inr": 0.0,
        "cost_status": "estimated",
    }

    for item in results:
        result_data = item.get("result") or {}
        usage = result_data.get("usage") or {}

        if isinstance(usage, dict):
            if usage:
                totals["usage_available"] = True

            totals["prompt_tokens"] += (
                usage.get("prompt_tokens", 0) or 0
            )
            totals["completion_tokens"] += (
                usage.get("completion_tokens", 0) or 0
            )
            totals["total_tokens"] += (
                usage.get("total_tokens", 0) or 0
            )

        totals["estimated_cost_inr"] += (
            item.get("estimated_cost_inr", 0) or 0
        )

    totals["estimated_cost_inr"] = round(
        totals["estimated_cost_inr"],
        4,
    )

    return totals