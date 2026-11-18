
class BudgetExceededError(Exception):
    """Raised when the agent exceeds its execution budget."""


class ExecutionBudget:
    """Track and enforce the agent's tool-call budget."""

    def __init__(self, max_tool_calls=10):
        if not isinstance(max_tool_calls, int) or isinstance(
            max_tool_calls, bool
        ) or max_tool_calls <= 0:
            raise ValueError(
                "max_tool_calls must be a positive integer."
            )

        self.max_tool_calls = max_tool_calls
        self.tool_calls_used = 0

    @property
    def remaining(self):
        return self.max_tool_calls - self.tool_calls_used

    def reserve_tool_call(self):
        """Reserve one tool call before execution."""

        if self.remaining <= 0:
            raise BudgetExceededError(
                "Tool-call budget exceeded. "
                f"Maximum allowed: {self.max_tool_calls}"
            )

        self.tool_calls_used += 1

    def report(self):
        """Return the current budget status."""

        return {
            "max_tool_calls": self.max_tool_calls,
            "tool_calls_used": self.tool_calls_used,
            "remaining": self.remaining,
            "budget_exhausted": self.remaining == 0,
        }
