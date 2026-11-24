
"""Offline tests for Gemini planner retry behavior."""

import httpx

from openai import APIConnectionError, APIStatusError

from bodhan_agent.planner_retry import create_plan_with_retry


def status_error(code):
    request = httpx.Request(
        "POST",
        "https://example.invalid/planner",
    )

    response = httpx.Response(
        code,
        request=request,
    )

    return APIStatusError(
        "simulated",
        response=response,
        body=None,
    )


def run():
    print(
        "Testing Gemini planner retries (offline)...\n"
    )

    sleeps = []
    events = []
    calls = []

    # TEST 1: Recover from HTTP 503
    def flaky(**kwargs):
        calls.append(kwargs)

        if len(calls) < 3:
            raise status_error(503)

        return {"action": "finish"}

    result = create_plan_with_retry(
        flaky,
        max_attempts=3,
        initial_delay=0.01,
        sleep_fn=sleeps.append,
        on_retry=lambda *args: events.append(args),
        model="fake-model",
    )

    assert result == {"action": "finish"}
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert len(events) == 2

    print(
        "PASS: 503 failures recover after two retries"
    )

    # TEST 2: Retry HTTP 429 and 5xx
    for code in (429, 500, 502, 504):
        count = [0]

        def temporary(**kwargs):
            count[0] += 1

            if count[0] == 1:
                raise status_error(code)

            return "ok"

        result = create_plan_with_retry(
            temporary,
            sleep_fn=lambda _: None,
        )

        assert result == "ok"
        assert count[0] == 2

    print(
        "PASS: 429 and 5xx errors are retryable"
    )

    # TEST 3: Do not retry permanent errors
    for code in (400, 401, 403, 404):
        count = [0]

        def permanent(**kwargs):
            count[0] += 1
            raise status_error(code)

        try:
            create_plan_with_retry(
                permanent,
                sleep_fn=lambda _: None,
            )

        except APIStatusError as exc:
            assert exc.status_code == code
            assert count[0] == 1

        else:
            raise AssertionError(
                f"HTTP {code} unexpectedly retried"
            )

    print(
        "PASS: Permanent 4xx errors are not retried"
    )

    # TEST 4: Connection error recovery
    count = [0]

    def disconnected(**kwargs):
        count[0] += 1

        if count[0] == 1:
            raise APIConnectionError(
                request=httpx.Request(
                    "POST",
                    "https://example.invalid",
                )
            )

        return "ok"

    result = create_plan_with_retry(
        disconnected,
        sleep_fn=lambda _: None,
    )

    assert result == "ok"
    assert count[0] == 2

    print(
        "PASS: Connection errors are retried"
    )

    # TEST 5: Enforce retry limit
    count = [0]

    def always_down(**kwargs):
        count[0] += 1
        raise status_error(503)

    try:
        create_plan_with_retry(
            always_down,
            max_attempts=3,
            sleep_fn=lambda _: None,
        )

    except APIStatusError:
        assert count[0] == 3

    else:
        raise AssertionError(
            "Retry limit was ignored"
        )

    print(
        "PASS: Retry limit is enforced"
    )

    print(
        "\nPASS: All Gemini planner retry tests passed!"
    )


if __name__ == "__main__":
    run()
