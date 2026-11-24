from __future__ import annotations

from pathlib import Path

import pytest

import bodhan_mcp.utils.files as files
from bodhan_mcp.exceptions import BodhanToolError


def test_resolve_input_rejects_outside_hidden_and_missing(tmp_path: Path) -> None:
    root = tmp_path / "in"
    root.mkdir()
    (root / "ok.txt").write_text("x")
    (root / ".secret").write_text("x")
    (tmp_path / "outside.txt").write_text("x")
    assert files.resolve_input_file("ok.txt", root, (root,)) == (root / "ok.txt").resolve()
    for raw, code in [
        ("../outside.txt", "path_not_allowed"),
        (".secret", "path_not_allowed"),
        ("nope.txt", "file_not_found"),
    ]:
        with pytest.raises(BodhanToolError) as info:
            files.resolve_input_file(raw, root, (root,))
        assert info.value.code == code


def test_write_bytes_atomic_refuses_overwrite_and_input(tmp_path: Path) -> None:
    target = tmp_path / "a.bin"
    files.write_bytes_atomic(target, b"1")
    with pytest.raises(BodhanToolError) as info:
        files.write_bytes_atomic(target, b"2")
    assert info.value.code == "file_exists"
    files.write_bytes_atomic(target, b"3", overwrite=True)
    assert target.read_bytes() == b"3"
    with pytest.raises(BodhanToolError):
        files.write_bytes_atomic(target, b"4", overwrite=True, protected=[target])
    assert [p.name for p in tmp_path.iterdir()] == ["a.bin"]


def test_write_pair_picks_free_names(tmp_path: Path) -> None:
    first = files.write_pair_atomic(tmp_path / "a.txt", "T", ".segments.json", "{}", explicit=False, overwrite=False)
    second = files.write_pair_atomic(tmp_path / "a.txt", "T", ".segments.json", "{}", explicit=False, overwrite=False)
    assert [p.name for p in first] == ["a.txt", "a.segments.json"]
    assert [p.name for p in second] == ["a-2.txt", "a-2.segments.json"]
    with pytest.raises(BodhanToolError) as info:
        files.write_pair_atomic(tmp_path / "a.txt", "T", ".segments.json", "{}", explicit=True, overwrite=False)
    assert info.value.code == "file_exists"


def test_write_pair_cleans_up_only_its_own_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = files.write_text_atomic

    def fail_markdown(path: Path, text: str, **kwargs: object) -> Path:
        if path.suffix == ".md":
            raise OSError("disk full")
        return real(path, text, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(files, "write_text_atomic", fail_markdown)
    with pytest.raises(OSError):
        files.write_pair_atomic(tmp_path / "b.md", "M", ".blocks.json", "{}", explicit=True, overwrite=False)
    assert not (tmp_path / "b.blocks.json").exists()

    (tmp_path / "c.blocks.json").write_text("old")
    with pytest.raises(OSError):
        files.write_pair_atomic(tmp_path / "c.md", "M", ".blocks.json", "{}", explicit=True, overwrite=True)
    assert (tmp_path / "c.blocks.json").exists()


def test_read_text_file_limits_and_encoding(tmp_path: Path) -> None:
    good = tmp_path / "good.txt"
    good.write_text("नमस्ते", encoding="utf-8")
    assert files.read_text_file(good, 100) == "नमस्ते"
    with pytest.raises(BodhanToolError) as info:
        files.read_text_file(good, 3)
    assert info.value.code == "input_too_large"
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(BodhanToolError) as info:
        files.read_text_file(bad, 100)
    assert info.value.code == "unsupported_encoding"
