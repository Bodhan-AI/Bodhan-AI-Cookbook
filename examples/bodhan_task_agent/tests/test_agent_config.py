from __future__ import annotations

import os
from pathlib import Path

import pytest

from bodhan_agent.core.config import (
    DEFAULT_DEADLINE_S,
    DEFAULT_MAX_COST_INR,
    DEFAULT_MAX_STEPS,
    LlmSettings,
    RunSettings,
    cache_dir,
)

LLM_ENV = {"LLM_BASE_URL": "https://llm.example/v1/", "LLM_MODEL": "test-model", "LLM_API_KEY": "sk-secret"}


def test_llm_settings_from_env() -> None:
    settings = LlmSettings.from_env(LLM_ENV)
    assert settings.base_url == "https://llm.example/v1"
    assert settings.model == "test-model"
    assert settings.api_key == "sk-secret"
    assert settings.configured is True
    assert settings.missing == []


def test_llm_defaults() -> None:
    settings = LlmSettings.from_env(LLM_ENV)
    assert (settings.timeout_s, settings.max_retries, settings.temperature, settings.max_output_tokens) == (
        90.0,
        2,
        0.0,
        4096,
    )


def test_missing_llm_values_are_listed_not_raised() -> None:
    settings = LlmSettings.from_env({"LLM_MODEL": "  "})
    assert settings.missing == ["LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"]
    assert settings.configured is False


def test_api_key_hidden_from_repr() -> None:
    assert "sk-secret" not in repr(LlmSettings.from_env(LLM_ENV))


@pytest.mark.parametrize("raw", ["none", "NULL", "off", ""])
def test_temperature_can_be_disabled(raw: str) -> None:
    assert LlmSettings.from_env({**LLM_ENV, "LLM_TEMPERATURE": raw}).temperature is None


def test_temperature_value_parsed() -> None:
    assert LlmSettings.from_env({**LLM_ENV, "LLM_TEMPERATURE": "0.7"}).temperature == 0.7


def test_bad_numbers_fall_back_to_defaults() -> None:
    settings = LlmSettings.from_env(
        {**LLM_ENV, "LLM_TIMEOUT_S": "soon", "LLM_MAX_RETRIES": "many", "LLM_TEMPERATURE": "warm"}
    )
    assert (settings.timeout_s, settings.max_retries, settings.temperature) == (90.0, 2, 0.0)


def test_numbers_are_clamped() -> None:
    settings = LlmSettings.from_env(
        {**LLM_ENV, "LLM_TIMEOUT_S": "0", "LLM_MAX_RETRIES": "-3", "LLM_MAX_OUTPUT_TOKENS": "10"}
    )
    assert (settings.timeout_s, settings.max_retries, settings.max_output_tokens) == (1.0, 0, 256)


def test_out_of_range_temperature_rejected() -> None:
    with pytest.raises(ValueError, match="temperature"):
        LlmSettings.from_env({**LLM_ENV, "LLM_TEMPERATURE": "5"})


def test_run_defaults(tmp_path: Path) -> None:
    settings = RunSettings.create("Translate this", [str(tmp_path)], str(tmp_path / "out"))
    assert (settings.max_steps, settings.max_cost_inr, settings.deadline_s) == (
        DEFAULT_MAX_STEPS,
        DEFAULT_MAX_COST_INR,
        DEFAULT_DEADLINE_S,
    )
    assert settings.llm_turn_limit == 2 * DEFAULT_MAX_STEPS
    assert settings.max_parallel_calls == 6


def test_explicit_llm_turn_limit(tmp_path: Path) -> None:
    settings = RunSettings(task="t", files=(), out_dir=tmp_path, max_llm_turns=7)
    assert settings.llm_turn_limit == 7


def test_paths_are_absolute_and_deduplicated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "inputs").mkdir()
    settings = RunSettings.create("t", ["inputs", "./inputs", "inputs/../inputs"], "out")
    assert settings.files == (tmp_path / "inputs",)
    assert settings.out_dir == tmp_path / "out"


def test_missing_files_are_kept_for_preflight(tmp_path: Path) -> None:
    settings = RunSettings.create("t", [str(tmp_path / "nope.wav")], str(tmp_path / "out"))
    assert settings.files == (tmp_path / "nope.wav",)


def test_output_layout(tmp_path: Path) -> None:
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    out = tmp_path / "out"
    assert settings.artifacts_dir == out / "artifacts"
    assert settings.work_dir == out / "work"
    assert settings.literals_dir == out / "work" / "literals"
    assert (settings.trace_path, settings.report_path, settings.result_path) == (
        out / "trace.jsonl",
        out / "report.json",
        out / "result.md",
    )
    assert settings.server_log_path == out / "work" / "server.log"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_steps", 0),
        ("max_cost_inr", -1.0),
        ("deadline_s", 0.0),
        ("max_llm_turns", 0),
        ("max_parallel_calls", 0),
        ("read_max_chars", 0),
        ("mcp_grace_s", -1.0),
    ],
)
def test_invalid_run_settings_rejected(tmp_path: Path, field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        RunSettings(task="t", files=(), out_dir=tmp_path, **{field: value})


def test_relative_paths_rejected_by_constructor() -> None:
    with pytest.raises(ValueError, match="out_dir"):
        RunSettings(task="t", files=(), out_dir=Path("out"))
    with pytest.raises(ValueError, match="files"):
        RunSettings(task="t", files=(Path("a.wav"),), out_dir=Path("/tmp/out"))


def test_zero_budget_allowed(tmp_path: Path) -> None:
    assert RunSettings(task="t", files=(), out_dir=tmp_path, max_cost_inr=0).max_cost_inr == 0


def test_server_input_dir_single_folder(tmp_path: Path) -> None:
    folder = tmp_path / "inputs"
    folder.mkdir()
    assert RunSettings.create("t", [str(folder)], str(tmp_path / "out")).server_input_dir == folder


def test_server_input_dir_common_parent_of_files(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "x.wav").write_bytes(b"x")
    (tmp_path / "b" / "y.pdf").write_bytes(b"y")
    settings = RunSettings.create("t", [str(tmp_path / "a" / "x.wav"), str(tmp_path / "b" / "y.pdf")], str(tmp_path))
    assert settings.server_input_dir == tmp_path


def test_server_input_dir_ignores_missing_paths(tmp_path: Path) -> None:
    (tmp_path / "x.wav").write_bytes(b"x")
    settings = RunSettings.create("t", [str(tmp_path / "x.wav"), "/does/not/exist.pdf"], str(tmp_path / "out"))
    assert settings.server_input_dir == tmp_path


def test_server_input_dir_falls_back_to_work_dir(tmp_path: Path) -> None:
    settings = RunSettings.create("t", [str(tmp_path / "missing.wav")], str(tmp_path / "out"))
    assert settings.server_input_dir == tmp_path / "out" / "work"


def test_server_env_points_server_at_run_folders(tmp_path: Path) -> None:
    (tmp_path / "x.wav").write_bytes(b"x")
    settings = RunSettings.create(
        "t", [str(tmp_path / "x.wav"), str(tmp_path / "gone.pdf")], str(tmp_path / "out"), max_cost_inr=1.5
    )
    env = settings.server_env({"PATH": "/bin", "BODHAN_API_KEY": "k"})
    assert env["BODHAN_MCP_INPUT_DIR"] == str(tmp_path)
    assert env["BODHAN_MCP_ALLOWED_DIRS"] == str(tmp_path / "x.wav")
    assert env["BODHAN_MCP_OUTPUT_DIR"] == str(tmp_path / "out" / "work")
    assert env["BODHAN_MCP_LOG_FILE"] == str(tmp_path / "out" / "work" / "server.log")
    assert env["BODHAN_MCP_MAX_COST_INR"] == "1.500000"
    assert env["BODHAN_API_KEY"] == "k"
    assert env["PATH"] == "/bin"


def test_server_env_joins_allowed_paths_with_os_separator(tmp_path: Path) -> None:
    for name in ("a.wav", "b.wav"):
        (tmp_path / name).write_bytes(b"x")
    settings = RunSettings.create("t", [str(tmp_path / "a.wav"), str(tmp_path / "b.wav")], str(tmp_path / "out"))
    allowed = settings.server_env({})["BODHAN_MCP_ALLOWED_DIRS"].split(os.pathsep)
    assert allowed == [str(tmp_path / "a.wav"), str(tmp_path / "b.wav")]


def test_server_env_never_passes_llm_secrets(tmp_path: Path) -> None:
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    env = settings.server_env(LLM_ENV)
    assert {name: env[name] for name in LLM_ENV} == {name: "" for name in LLM_ENV}


def test_blank_llm_values_block_dotenv_reload(tmp_path: Path) -> None:
    from dotenv import load_dotenv

    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text("LLM_API_KEY=sk-from-file\nBODHAN_API_KEY=bodhan-file\n")
    env = RunSettings.create("t", [], str(tmp_path / "out")).server_env({})
    import os

    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        load_dotenv(dotenv_file)
        assert os.environ["LLM_API_KEY"] == ""
        assert os.environ["BODHAN_API_KEY"] == "bodhan-file"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_server_env_does_not_mutate_base(tmp_path: Path) -> None:
    base = dict(LLM_ENV)
    RunSettings.create("t", [], str(tmp_path / "out")).server_env(base)
    assert base == LLM_ENV


def test_cache_dir_defaults_outside_run_folder(tmp_path: Path) -> None:
    assert cache_dir({"XDG_CACHE_HOME": str(tmp_path / "xdg")}) == tmp_path / "xdg" / "bodhan_agent"
    assert cache_dir({}) == Path.home().resolve() / ".cache" / "bodhan_agent"


def test_cache_dir_honours_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert cache_dir({"BODHAN_CACHE_DIR": "./.cache/bodhan"}) == tmp_path / ".cache" / "bodhan"
    assert cache_dir({"BODHAN_CACHE_DIR": str(tmp_path / "abs")}) == tmp_path / "abs"


def test_server_env_sets_persistent_cache(tmp_path: Path) -> None:
    settings = RunSettings.create("t", [], str(tmp_path / "out"))
    env = settings.server_env({"XDG_CACHE_HOME": str(tmp_path / "xdg")})
    assert env["BODHAN_CACHE_DIR"] == str(tmp_path / "xdg" / "bodhan_agent")
    assert not Path(env["BODHAN_CACHE_DIR"]).is_relative_to(settings.out_dir)
