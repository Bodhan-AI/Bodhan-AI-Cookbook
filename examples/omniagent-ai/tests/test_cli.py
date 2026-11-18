
from pathlib import Path

from bodhan_agent.cli import parse_args


def main():
    print("Testing OmniAgent AI CLI...\n")

    args = parse_args([
        "--task",
        "Extract Hindi text and translate it into English.",
        "--files",
        "data",
        "--out",
        "out",
        "--max-steps",
        "5",
        "--max-tool-calls",
        "3",
    ])

    assert args.task == (
        "Extract Hindi text and translate it into English."
    )
    assert args.files == Path("data")
    assert args.out == Path("out")
    assert args.max_steps == 5
    assert args.max_tool_calls == 3
    assert args.max_cost_inr is None

    print("PASS: Task argument parsed!")
    print("PASS: Input/output paths parsed!")
    print("PASS: Execution limits parsed!")

    default_args = parse_args([
        "--task",
        "Translate a sentence.",
    ])

    assert default_args.max_steps == 30
    assert default_args.max_tool_calls == 10

    print("PASS: Default arguments verified!")

    try:
        parse_args([
            "--task",
            "   ",
        ])
    except SystemExit:
        print("PASS: Empty tasks rejected!")
    else:
        raise AssertionError(
            "Empty task was accepted."
        )

    print("\nPASS: CLI argument parser works correctly!")


if __name__ == "__main__":
    main()
