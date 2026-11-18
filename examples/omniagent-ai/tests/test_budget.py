
from bodhan_agent.budget import (
    ExecutionBudget,
    BudgetExceededError,
)


def main():
    print("Testing OmniAgent AI Execution Budget...\n")

    budget = ExecutionBudget(max_tool_calls=2)

    # First tool call
    budget.reserve_tool_call()
    print("Tool call 1 reserved successfully.")

    # Second tool call
    budget.reserve_tool_call()
    print("Tool call 2 reserved successfully.")

    print("\nBudget report:")
    print(budget.report())

    assert budget.tool_calls_used == 2
    assert budget.remaining == 0
    assert budget.report()["budget_exhausted"] is True

    # Third tool call must be blocked
    try:
        budget.reserve_tool_call()
    except BudgetExceededError:
        print("\nPASS: Tool-call limit enforced!")
    else:
        raise AssertionError(
            "Budget manager allowed an extra tool call!"
        )

    assert budget.tool_calls_used == 2

    print("PASS: Budget tracking works correctly!")


if __name__ == "__main__":
    main()

