import json

import pytest

from bodhan_eval.cli import main, parse_config
from bodhan_eval.config import load_dotenv

FULL_ENV = {
    "BODHAN_TRANSLATE_KEY": "secret-translate",
    "BODHAN_SPEAK_KEY": "secret-speak",
    "BODHAN_TRANSCRIBE_KEY": "secret-transcribe",
    "LLM_BASE_URL": "https://llm.example/v1",
    "LLM_MODEL": "some-model",
    "LLM_API_KEY": "secret-llm",
}


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text('{"id": "a", "source": "Hi.", "lang": "hi"}\n', encoding="utf-8")
    return path


def test_required_interface(dataset, tmp_path):
    cfg = parse_config(
        ["--dataset", str(dataset), "--langs", " HI,ta,bn,hi ", "--out", str(tmp_path / "r"), "--generate", "--max-cost-inr", "50"],
        env=FULL_ENV,
    )
    assert cfg.langs == ("hi", "ta", "bn")  # lowercased, deduped, order kept
    assert parse_config(["--dataset", str(dataset), "--langs", "mni-beng,ks-Deva", "--out", "r"], env={}).langs == (
        "mni-Beng", "ks-Deva",
    )
    assert cfg.generate and cfg.max_cost_inr == 50
    assert cfg.llm_enabled and cfg.has_key("speak")
    assert cfg.warnings() == []
    assert "secret" not in repr(cfg)


@pytest.mark.parametrize(
    "extra",
    [
        ["--langs", "hindi"],
        ["--langs", ","],
        ["--langs", "hi,hindi"],
        ["--langs", "hi", "--max-cost-inr", "-1"],
        ["--langs", "hi", "--calibration", "missing.jsonl"],
        ["--langs", "hi", "--baseline", "missing_dir"],
    ],
)
def test_bad_arguments_exit_2(dataset, tmp_path, extra):
    with pytest.raises(SystemExit) as e:
        parse_config(["--dataset", str(dataset), "--out", str(tmp_path / "r"), *extra], env={})
    assert e.value.code == 2


def test_missing_dataset_exits_2(tmp_path):
    with pytest.raises(SystemExit) as e:
        parse_config(["--dataset", str(tmp_path / "nope.jsonl"), "--langs", "hi", "--out", str(tmp_path)], env={})
    assert e.value.code == 2


def test_baseline_cannot_be_out(dataset, tmp_path):
    with pytest.raises(SystemExit):
        parse_config(["--dataset", str(dataset), "--langs", "hi", "--out", str(tmp_path), "--baseline", str(tmp_path)], env={})


def test_missing_credentials_degrade_not_fail(dataset, tmp_path):
    cfg = parse_config(["--dataset", str(dataset), "--langs", "hi", "--out", str(tmp_path), "--generate"], env={"LLM_MODEL": ""})
    assert not cfg.llm_enabled
    assert len(cfg.warnings()) == 4


def test_main_writes_run_json_without_secrets(dataset, tmp_path, monkeypatch):
    for k, v in FULL_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)  # no stray .env picked up
    out = tmp_path / "report"
    assert main(["--dataset", str(dataset), "--langs", "hi", "--out", str(out)]) == 0
    text = (out / "run.json").read_text(encoding="utf-8")
    assert "secret" not in text
    meta = json.loads(text)
    assert meta["status"] == "ok"
    assert meta["args"]["langs"] == ["hi"]
    assert meta["credentials_present"] == {"translate": True, "speak": True, "transcribe": True, "llm": True}
    assert len(meta["args"]["dataset_sha256"]) == 64
    assert meta["dataset"] == {"items": 1, "skipped": 0, "other_langs": 0, "items_with_warnings": 0}


def test_dotenv_never_overrides_real_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nexport LLM_MODEL='from-file'\nLLM_BASE_URL=from-file\n\nnot a pair\n", encoding="utf-8")
    monkeypatch.setenv("LLM_BASE_URL", "from-env")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    load_dotenv(env_file)
    import os

    assert os.environ["LLM_MODEL"] == "from-file"
    assert os.environ["LLM_BASE_URL"] == "from-env"
