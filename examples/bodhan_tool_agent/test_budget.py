import asyncio

from bodhan_agent.executor import execute_plan, ExecutionError


class FakeTool:
    def __init__(self, name, input_schema=None):
        self.name = name
        self.description = "Fake test tool"
        self.inputSchema = input_schema or {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }


class FakeSession:
    def __init__(self):
        self.called = False

    async def list_tools(self):
        return [FakeTool("indic_translate")]

    async def call_tool(self, name, arguments):
        self.called = True
        raise AssertionError("Tool should not be called due to budget limit")


def test_budget_enforcement():
    async def run_test():
        session = FakeSession()

        plan = {
            "steps": [
                {
                    "tool": "indic_translate",
                    "description": "Translate test text",
                    "input": {"text": "Hello"},
                }
            ]
        }

        try:
            await execute_plan(
                session=session,
                plan=plan,
                files=[],
                task="Test budget enforcement",
                max_steps=5,
                max_cost_inr=0.10,
            )
        except ExecutionError as exc:
            assert session.called is False
            assert exc.budget_summary["estimated_spent_inr"] == 0.0
        else:
            raise AssertionError("Execution was not blocked by budget")

    asyncio.run(run_test())