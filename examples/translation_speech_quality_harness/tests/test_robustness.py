import json
import wave
from pathlib import Path

import numpy as np
import pytest

from bodhan_eval.budget import Budget
from bodhan_eval.dataset import read_dataset
from bodhan_eval.pipeline import run_pipeline
from bodhan_eval.report import write_outputs


def _write_rows(path: Path, lines: list[str]) -> None:
    path.write_bytes(("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8"))


def _row(**overrides) -> dict:
    row = {
        "id": "safe-id",
        "lang": "hi",
        "source": "Hello",
        "translation": "नमस्ते",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "line",
    [
        "{bad",
        '{"id": "comma",}',
        "[]",
        '"string"',
        "null",
        "3",
        json.dumps({"id": "missing-lang", "source": "hello"}),
        json.dumps(_row(source={"unexpected": "object"})),
    ],
)
def test_malformed_jsonl_records_are_quarantined_without_crashing(tmp_path: Path, line: str):
    dataset = tmp_path / "input.jsonl"
    _write_rows(dataset, [line])

    result = read_dataset(dataset)

    assert result.total_input_lines == 1
    assert len(result.quarantined) == 1
    assert not result.items


def test_invalid_utf8_and_large_lines_are_quarantined(tmp_path: Path):
    dataset = tmp_path / "invalid.jsonl"
    dataset.write_bytes(b'{"id":"bad","lang":"hi","source":"\xff"}\n')
    result = read_dataset(dataset)
    assert len(result.quarantined) == 1
    assert "invalid_utf8" in result.quarantined[0].reason

    large = tmp_path / "large.jsonl"
    large.write_bytes(
        b'{"id":"large","lang":"hi","source":"' + b"x" * (5 * 1024 * 1024) + b'"}\n'
    )
    result = read_dataset(large)
    assert len(result.quarantined) == 1
    assert "line_too_large" in result.quarantined[0].reason


def test_language_aliases_types_labels_and_surrogates_are_safe(tmp_path: Path):
    dataset = tmp_path / "aliases.jsonl"
    rows = [
        _row(id=7, lang="HI-IN", translation="नमस्ते", source="safe"),
        _row(id="wrong-type", translation=123),
        _row(id="regional", lang="hi-" "IN", source="second"),
        _row(id="tai", lang="TA", source="third", translation="தமிழ்"),
        _row(id="bad-label", labels=[{"type": "number", "span": [0]}]),
        _row(id="null-labels", labels=None),
        _row(id="surrogate", source="bad\ud800value"),
    ]
    dataset.write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    result = read_dataset(dataset)

    by_id = {item.id: item for item in result.items}
    assert by_id["regional"].lang == "hi"
    assert by_id["tai"].lang == "ta"
    assert by_id["bad-label"].labels[0].type == "unmapped"
    assert "\\ud800" in by_id["surrogate"].source
    assert by_id["7"].lang == "hi"
    assert any("wrong_type:translation" in record.reason for record in result.quarantined)
    assert any("unmapped_label_span" in warning for warning in result.warnings)
    assert any("unmapped_labels" in warning for warning in result.warnings)


def _write_wav(path: Path, duration_s: float = 1.0) -> None:
    samples = np.zeros(int(24_000 * duration_s), dtype="<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24_000)
        stream.writeframes(samples.tobytes())


@pytest.mark.parametrize(
    "audio",
    ["../escape.wav", "https://example.invalid/audio.wav", ""],
)
def test_unsafe_audio_paths_are_quarantined(tmp_path: Path, audio: str):
    dataset = tmp_path / "input.jsonl"
    _write_rows(dataset, [json.dumps(_row(audio=audio))])

    result = read_dataset(dataset)

    assert not result.items
    assert len(result.quarantined) == 1
    assert "audio_path" in result.quarantined[0].reason


def test_absolute_directory_and_missing_audio_are_refused(tmp_path: Path):
    dataset = tmp_path / "input.jsonl"
    directory = tmp_path / "audio-dir"
    directory.mkdir()
    rows = [
        json.dumps(_row(id="absolute", audio=str(directory / "a.wav"))),
        json.dumps(_row(id="directory", audio="audio-dir")),
        json.dumps(_row(id="missing", audio="missing.wav")),
    ]
    _write_rows(dataset, rows)

    result = read_dataset(dataset)

    assert len(result.quarantined) == 3
    assert all("audio_path" in record.reason for record in result.quarantined)


def test_audio_duration_cap_and_symlink_escape(tmp_path: Path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    long_audio = audio_dir / "long.wav"
    _write_wav(long_audio, duration_s=31)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.wav"
    _write_wav(outside, duration_s=1)
    link = audio_dir / "escape.wav"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    dataset = tmp_path / "input.jsonl"
    _write_rows(dataset, [
        json.dumps(_row(id="long", audio="audio/long.wav")),
        json.dumps(_row(id="symlink", audio="audio/escape.wav")),
    ])
    result = read_dataset(dataset)

    assert len(result.quarantined) == 2
    assert any("audio_duration" in record.reason for record in result.quarantined)
    assert any("audio_path" in record.reason for record in result.quarantined)


@pytest.mark.parametrize("truncate_bytes", [0, 20])
def test_truncated_wav_is_quarantined_without_crashing(
    tmp_path: Path, truncate_bytes: int
):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio_path = audio_dir / "truncated.wav"
    _write_wav(audio_path)
    audio_path.write_bytes(audio_path.read_bytes()[:truncate_bytes])
    dataset = tmp_path / "input.jsonl"
    _write_rows(dataset, [json.dumps(_row(audio="audio/truncated.wav"))])

    result = read_dataset(dataset)

    assert not result.items
    assert len(result.quarantined) == 1
    assert result.quarantined[0].reason == "audio_path_invalid_wav"


def test_bad_row_and_hostile_text_still_produce_terminal_accounting(tmp_path: Path):
    dataset = tmp_path / "mixed.jsonl"
    _write_rows(dataset, [
        json.dumps(_row(id="html", source="<script>alert(1)</script>", translation="x|y")),
        "{broken",
        json.dumps(_row(id="null", source="A\u0000B", translation="")),
    ])
    result = read_dataset(dataset)
    records = run_pipeline(result, Budget(1.0))
    out = tmp_path / "report"
    write_outputs(out, records, Budget(1.0), input_lines=result.total_input_lines)

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["accounting"]["scored"] + summary["accounting"]["skipped"] + summary["accounting"]["quarantined"] == 3
    assert (out / "items.jsonl").is_file()
    assert (out / "summary.md").is_file()


def test_summary_escapes_hostile_item_identifiers(tmp_path: Path):
    dataset = tmp_path / "hostile.jsonl"
    _write_rows(dataset, [
        json.dumps(_row(id="<script>|**alert**", translation="")),
    ])
    result = read_dataset(dataset)
    budget = Budget(1.0)
    records = run_pipeline(result, budget)
    write_outputs(tmp_path / "report", records, budget, input_lines=result.total_input_lines)
    summary = (tmp_path / "report" / "summary.md").read_text(encoding="utf-8")

    assert "<script>" not in summary
    assert "&lt;script&gt;" in summary
