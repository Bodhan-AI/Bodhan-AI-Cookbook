import json
from pathlib import Path
from typing import Any

from bodhan_eval import cli
from bodhan_eval.client.bodhan import TranslateResult


class FakeBodhanClient:
    responses: list[TranslateResult]
    calls: list[tuple[str, str, int | None]]

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.responses = type(self).responses[:]
        self.calls = type(self).calls

    async def __aenter__(self) -> "FakeBodhanClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def translate(
        self,
        text: str,
        target_language: str,
        *,
        script: str = "native",
        max_output_tokens: int | None = None,
    ) -> TranslateResult:
        self.calls.append((text, target_language, max_output_tokens))
        return self.responses.pop(0)


def _write_dataset(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def _run_generation(
    monkeypatch,
    tmp_path: Path,
    rows: list[dict[str, Any]],
    responses: list[TranslateResult],
    max_cost: float = 1.0,
) -> tuple[int, dict[str, Any]]:
    dataset = tmp_path / "input.jsonl"
    output = tmp_path / "out"
    _write_dataset(dataset, rows)
    FakeBodhanClient.responses = responses
    FakeBodhanClient.calls = []
    monkeypatch.setattr(cli, "BODHAN_TRANSLATE_KEY", "test-key")
    monkeypatch.setattr(cli, "BodhanClient", FakeBodhanClient)

    status = cli.main([
        "--dataset", str(dataset),
        "--langs", "hi",
        "--out", str(output),
        "--generate",
        "--no-speech",
        "--max-cost-inr", str(max_cost),
    ])
    records = [
        json.loads(line)
        for line in (output / "items.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return status, {record["id"]: record for record in records}


def test_generate_translates_missing_text_before_checks(monkeypatch, tmp_path: Path) -> None:
    status, records = _run_generation(
        monkeypatch,
        tmp_path,
        [{"id": "missing", "lang": "hi", "source": "Value 100"}],
        [TranslateResult("मान 100", False, {"completion_tokens": 3})],
    )

    assert status == 0
    assert FakeBodhanClient.calls == [("Value 100", "hi", 512)]
    record = records["missing"]
    assert record["terminal_state"] == "scored"
    assert record["generation"]["status"] == "generated"
    assert record["generation"]["attempts"] == 1
    assert record["checks_run"]


def test_generate_retries_once_when_translation_is_truncated(monkeypatch, tmp_path: Path) -> None:
    status, records = _run_generation(
        monkeypatch,
        tmp_path,
        [{"id": "retry", "lang": "hi", "source": "A longer source sentence."}],
        [
            TranslateResult("अधूरा", True, {"completion_tokens": 512}),
            TranslateResult("पूरा अनुवाद", False, {"completion_tokens": 6}),
        ],
    )

    assert status == 0
    assert [call[2] for call in FakeBodhanClient.calls] == [512, 1024]
    assert records["retry"]["terminal_state"] == "scored"
    assert records["retry"]["generation"]["attempts"] == 2


def test_still_truncated_generation_is_unchecked_not_clean(monkeypatch, tmp_path: Path) -> None:
    status, records = _run_generation(
        monkeypatch,
        tmp_path,
        [{"id": "truncated", "lang": "hi", "source": "Source"}],
        [
            TranslateResult("अधूरा", True, {}),
            TranslateResult("अब भी अधूरा", True, {}),
        ],
    )

    record = records["truncated"]
    assert status == 0
    assert record["terminal_state"] == "failed_checks"
    assert record["flags"] == []
    assert record["generation"]["status"] == "failed"
    assert record["generation"]["reason"] == "translation_truncated"


def test_generate_does_not_replace_existing_translation_or_call_api(
    monkeypatch, tmp_path: Path
) -> None:
    status, records = _run_generation(
        monkeypatch,
        tmp_path,
        [{
            "id": "existing",
            "lang": "hi",
            "source": "Value 100",
            "translation": "मान 100",
        }],
        [],
    )

    assert status == 0
    assert FakeBodhanClient.calls == []
    assert records["existing"]["terminal_state"] == "scored"
    assert "generation" not in records["existing"]


def test_generation_budget_is_reserved_before_any_api_call(monkeypatch, tmp_path: Path) -> None:
    dataset = tmp_path / "input.jsonl"
    output = tmp_path / "out"
    _write_dataset(
        dataset,
        [{"id": "budget", "lang": "hi", "source": "Value 100"}],
    )
    FakeBodhanClient.responses = []
    FakeBodhanClient.calls = []
    monkeypatch.setattr(cli, "BODHAN_TRANSLATE_KEY", "test-key")
    monkeypatch.setattr(cli, "BodhanClient", FakeBodhanClient)

    status = cli.main([
        "--dataset", str(dataset),
        "--langs", "hi",
        "--out", str(output),
        "--generate",
        "--no-speech",
        "--max-cost-inr", "0.01",
    ])
    record = json.loads(
        (output / "items.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )

    assert status == 0
    assert FakeBodhanClient.calls == []
    assert record["terminal_state"] == "failed_checks"
    assert record["generation"]["reason"] == "budget_limit"
