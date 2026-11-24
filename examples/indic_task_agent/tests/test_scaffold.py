"""Scaffold smoke tests: the assignment's required interfaces must stay stable."""

import pytest

from bodhan_agent.cli import build_parser


def test_agent_required_flags():
    args = build_parser().parse_args(["--task", "t", "--files", "a", "b", "--out", "o"])
    assert args.task == "t" and args.files == ["a", "b"] and args.out == "o"
    assert args.max_steps == 30 and args.max_cost_inr == 20.0


def test_agent_task_and_out_are_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--files", "a"])


def test_mcp_package_importable():
    import bodhan_mcp.server  # noqa: F401
