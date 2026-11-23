"""Argument parsing for the optional flags (the required ones are covered by the end-to-end tests)."""

from __future__ import annotations

import pytest

from bodhan_eval.cli import build_parser, options_from_args

REQUIRED = ["--dataset", "d.jsonl", "--langs", "hi", "--out", "out"]


def parse(*extra: str):
    return options_from_args(build_parser().parse_args([*REQUIRED, *extra]))


def test_llm_extra_body_is_off_by_default() -> None:
    assert parse().llm_extra_body is None


def test_llm_extra_body_takes_a_json_object() -> None:
    opts = parse("--llm-extra-body", '{"reasoning": {"effort": "low"}}')
    assert opts.llm_extra_body == {"reasoning": {"effort": "low"}}


def test_judge_flags_default_to_defaults_json() -> None:
    opts = parse()
    assert (opts.judge_max_replicas, opts.judge_models, opts.judge_repeat_sample) == (None, None, None)


def test_judge_flags() -> None:
    opts = parse(
        "--judge-max-replicas", "5", "--judge-models", "a/x, b/y,a/x", "--judge-repeat-sample", "0"
    )  # fmt: skip
    assert opts.judge_max_replicas == 5
    assert opts.judge_models == ["a/x", "b/y"]  # repeats dropped, order kept
    assert opts.judge_repeat_sample == 0.0


@pytest.mark.parametrize(
    "flags",
    [["--judge-max-replicas", "0"], ["--judge-models", " , "], ["--judge-repeat-sample", "1.5"]],
    ids=["zero replicas", "empty jury", "share above 1"],
)
def test_judge_flags_reject_bad_values(flags: list[str]) -> None:
    with pytest.raises(SystemExit):
        parse(*flags)


@pytest.mark.parametrize("bad", ["not json", "[1, 2]", '"low"'])
def test_llm_extra_body_rejects_anything_but_an_object(bad: str) -> None:
    with pytest.raises(SystemExit):
        parse("--llm-extra-body", bad)
