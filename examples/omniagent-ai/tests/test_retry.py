
import asyncio

from httpx import ConnectError

from bodhan_agent.retry import retry_async


async def main():
    print("Testing OmniAgent AI Retry Handling...\n")

    attempts = 0
    retry_events = []

    async def temporary_failure():
        nonlocal attempts

        attempts += 1
        print(f"Attempt {attempts}")

        if attempts < 3:
            raise ConnectError(
                "Simulated temporary connection failure"
            )

        return {
            "status": "success",
            "message": "Recovered successfully",
        }

    def record_retry(**details):
        retry_events.append(details)
        print(
            "Retry scheduled:",
            details,
        )

    result = await retry_async(
        operation=temporary_failure,
        max_attempts=3,
        initial_delay=0.01,
        on_retry=record_retry,
    )

    assert attempts == 3
    assert result["status"] == "success"
    assert len(retry_events) == 2

    print("\nPASS: Temporary failures recovered!")

    permanent_attempts = 0

    async def permanent_failure():
        nonlocal permanent_attempts
        permanent_attempts += 1
        raise ValueError("Invalid arguments")

    try:
        await retry_async(
            operation=permanent_failure,
            max_attempts=3,
            initial_delay=0.01,
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "Permanent error should have been raised."
        )

    assert permanent_attempts == 1

    print("PASS: Permanent errors are not retried!")
    print("PASS: Retry handling works correctly!")


if __name__ == "__main__":
    asyncio.run(main())
