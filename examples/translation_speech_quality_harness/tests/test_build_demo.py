import importlib.util
import json
from pathlib import Path

import httpx
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_demo.py"
SPEC = importlib.util.spec_from_file_location("build_demo", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
build_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_demo)


def test_fetch_rows_paginates_and_sends_token():
    requested_offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rows"
        assert request.headers["authorization"] == "Bearer hf-test"
        assert request.url.params["dataset"] == "ai4bharat/IN22-Gen"
        assert request.url.params["config"] == "default"
        assert request.url.params["split"] == "test"
        offset = int(request.url.params["offset"])
        length = int(request.url.params["length"])
        requested_offsets.append(offset)
        end = min(offset + length, 101)
        rows = [
            {
                "row_idx": index,
                "row": {
                    "eng_Latn": f"Source {index}",
                    "hin_Deva": f"Translation {index}",
                },
            }
            for index in range(offset, end)
        ]
        return httpx.Response(
            200,
            json={"rows": rows, "num_rows_total": 101},
        )

    rows = build_demo.fetch_rows(
        "http://hf.test",
        "ai4bharat/IN22-Gen",
        "hf-test",
        101,
        transport=httpx.MockTransport(handler),
    )

    assert len(rows) == 101
    assert requested_offsets == [0, 100]


def test_fetch_rows_access_error_is_actionable_without_echoing_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with pytest.raises(build_demo.DatasetAccessError, match="accept.*terms") as error:
        build_demo.fetch_rows(
            "http://hf.test",
            "ai4bharat/IN22-Gen",
            "secret-token",
            1,
            transport=httpx.MockTransport(handler),
        )
    assert "secret-token" not in str(error.value)


def test_fetch_rows_requires_https_for_live_requests():
    with pytest.raises(ValueError, match="HTTPS"):
        build_demo.fetch_rows(
            "http://hf.test",
            "ai4bharat/IN22-Gen",
            "hf-test",
            1,
        )


def test_load_local_environment_reads_root_dotenv_without_overriding_process_env(
    tmp_path: Path, monkeypatch, capsys
):
    (tmp_path / ".env").write_text(
        "HF_TOKEN=token-from-file\nHF_DATASETS_SERVER_URL=https://hf.test\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_DATASETS_SERVER_URL", raising=False)

    token = build_demo.load_local_environment(tmp_path)

    assert token == "token-from-file"
    assert build_demo.os.environ["HF_DATASETS_SERVER_URL"] == "https://hf.test"
    captured = capsys.readouterr()
    assert "token-from-file" not in captured.out + captured.err


def test_load_local_environment_keeps_existing_process_token(
    tmp_path: Path, monkeypatch
):
    (tmp_path / ".env").write_text("HF_TOKEN=token-from-file\n", encoding="utf-8")
    monkeypatch.setenv("HF_TOKEN", "token-from-process")

    assert build_demo.load_local_environment(tmp_path) == "token-from-process"


def test_rows_to_items_maps_language_and_preserves_reference():
    records = build_demo.rows_to_items(
        [
            {
                "id": 7,
                "domain": "health",
                "eng_Latn": "Drink clean water.",
                "hin_Deva": "स्वच्छ पानी पिएँ।",
                "tam_Taml": "சுத்தமான தண்ணீரைக் குடிக்கவும்.",
            }
        ],
        ["hi", "ta"],
        "IN22-Gen",
    )

    assert [(row["id"], row["lang"]) for row in records] == [
        ("in22-gen-7-hi", "hi"),
        ("in22-gen-7-ta", "ta"),
    ]
    assert records[0]["source"] == "Drink clean water."
    assert records[0]["translation"] == records[0]["reference"] == "स्वच्छ पानी पिएँ।"
    assert records[0]["dataset_domain"] == "health"
    assert records[0]["dataset_license"] == "CC BY 4.0"


def test_write_jsonl_refuses_to_replace_existing_output(tmp_path: Path):
    output = tmp_path / "existing.jsonl"
    output.write_text("keep\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        build_demo.write_jsonl(output, [{"id": "new"}], force=False)

    assert output.read_text(encoding="utf-8") == "keep\n"


def test_write_jsonl_is_sorted_and_uses_stable_json(tmp_path: Path):
    output = tmp_path / "rows.jsonl"
    build_demo.write_jsonl(
        output,
        [{"id": "b", "lang": "ta"}, {"id": "a", "lang": "hi"}],
        force=False,
    )

    content = output.read_text(encoding="utf-8")
    assert content == (
        '{"id": "a", "lang": "hi"}\n'
        '{"id": "b", "lang": "ta"}\n'
    )
    assert [json.loads(line)["id"] for line in content.splitlines()] == ["a", "b"]


def test_in22_dry_run_needs_no_token_or_network(monkeypatch, capsys, tmp_path: Path):
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_demo.py",
            "--source",
            "in22-gen",
            "--langs",
            "hi,ta",
            "--limit-per-language",
            "101",
            "--out",
            str(tmp_path / "in22.jsonl"),
            "--dry-run",
        ],
    )
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_DATASETS_SERVER_URL", raising=False)

    assert build_demo.main() == 0
    output = capsys.readouterr().out
    assert "Estimated dataset download requests: 2" in output
    assert "Bodhan model calls: 0" in output
    assert not (tmp_path / "in22.jsonl").exists()


def test_main_fetches_default_test_and_maps_all_languages(
    monkeypatch, tmp_path: Path
):
    calls: list[tuple[str, int]] = []

    def fake_fetch_rows(
        _base: str,
        _dataset: str,
        _token: str,
        limit: int,
    ) -> list[dict]:
        calls.append(("default/test", limit))
        return [
            {
                "id": index,
                "eng_Latn": f"Source {index}",
                "hin_Deva": f"Translation hi {index}",
                "tam_Taml": f"Translation ta {index}",
            }
            for index in range(limit)
        ]

    monkeypatch.setattr(build_demo, "fetch_rows", fake_fetch_rows)
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_demo.py",
            "--source",
            "in22-gen",
            "--langs",
            "hi,ta",
            "--limit-per-language",
            "2",
            "--api-base-url",
            "https://hf.test",
            "--out",
            str(tmp_path / "in22.jsonl"),
        ],
    )
    monkeypatch.setenv("HF_TOKEN", "hf-test")

    assert build_demo.main() == 0
    assert calls == [("default/test", 2)]
    records = [
        json.loads(line)
        for line in (tmp_path / "in22.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 4
    assert {row["lang"] for row in records} == {"hi", "ta"}


def test_fetch_rows_404_reports_dataset_server_availability():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not found."})

    with pytest.raises(build_demo.DatasetAccessError, match="config=default, split=test"):
        build_demo.fetch_rows(
            "http://hf.test",
            "ai4bharat/IN22-Conv",
            "hf-test",
            1,
            transport=httpx.MockTransport(handler),
        )
