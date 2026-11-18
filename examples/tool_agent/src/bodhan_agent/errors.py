class ToolError(Exception):
    """An actionable error safe to return to an MCP client or the task agent."""

    def __init__(self, message: str, code: str = "tool_error") -> None:
        super().__init__(message)
        self.code = code


class BudgetExceeded(ToolError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "budget_exceeded")
