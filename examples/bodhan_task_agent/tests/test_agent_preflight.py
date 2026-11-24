from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from bodhan_agent.core import guards
from bodhan_agent.core.config import RunSettings
from bodhan_agent.pipeline import preflight
from bodhan_agent.schemas.ledger import Ledger

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAS_PDFINFO = shutil.which("pdfinfo") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
needs_pdfinfo = pytest.mark.skipif(not HAS_PDFINFO, reason="pdfinfo not installed")

HINDI = "नमस्ते दुनिया"
TAMIL = "வணக்கம் உலகம், இது ஒரு சோதனை."


def make_pdf(path: Path, pages: int) -> Path:
    kids = " ".join(f"{3 + index} 0 R" for index in range(pages))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>",
        *["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"] * pages,
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    body += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(body)
    return path


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def make_wav(path: Path, seconds: float = 1.5) -> Path:
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(path))
    return path


def settings_for(tmp_path: Path, task: str = "Do the task", files: list[Path] | None = None) -> RunSettings:
    return RunSettings.create(task, [str(path) for path in files or []], str(tmp_path / "out"))


async def inspect(path: Path):
    return await preflight.inspect_input(path, "f1", deadline=10**12)


def test_collect_walks_folders_in_order(tmp_path: Path) -> None:
    root = tmp_path / "in"
    (root / "sub").mkdir(parents=True)
    for name in ("b.txt", "a.txt", "sub/c.txt"):
        (root / name).write_text("x")
    found, issues = preflight.collect_inputs(settings_for(tmp_path, files=[root]))
    assert [path.relative_to(root).as_posix() for path in found] == ["a.txt", "b.txt", "sub/c.txt"]
    assert issues == []


def test_collect_skips_hidden_links_and_out_dir(tmp_path: Path) -> None:
    root = tmp_path
    (root / "a.txt").write_text("x")
    (root / ".secret").write_text("x")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("x")
    (root / "link.txt").symlink_to(root / "a.txt")
    (root / "out" / "work").mkdir(parents=True)
    (root / "out" / "work" / "old.md").write_text("x")
    found, issues = preflight.collect_inputs(settings_for(tmp_path, files=[root]))
    assert found == [root / "a.txt"]
    assert sorted(issue.code for issue in issues) == ["skipped_hidden", "skipped_hidden", "skipped_link"]


def test_collect_reports_missing_and_empty(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    found, issues = preflight.collect_inputs(settings_for(tmp_path, files=[tmp_path / "nope.wav", tmp_path / "empty"]))
    assert found == []
    assert [issue.code for issue in issues] == ["missing_input", "empty_input"]


def test_collect_deduplicates_and_keeps_explicit_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    found, _ = preflight.collect_inputs(settings_for(tmp_path, files=[tmp_path / "a.txt", tmp_path]))
    assert found == [tmp_path / "a.txt"]


def test_collect_caps_file_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "MAX_INPUT_FILES", 2)
    root = tmp_path / "in"
    root.mkdir()
    for index in range(4):
        (root / f"{index}.txt").write_text("x")
    found, issues = preflight.collect_inputs(settings_for(tmp_path, files=[root]))
    assert len(found) == 2
    assert issues[-1].code == "too_many_files"
    assert "4 files" in issues[-1].message


async def test_english_text_file(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("Plants make food from sunlight.\n")
    artifact, issues = await inspect(path)
    assert (artifact.kind, artifact.step_status, artifact.script, artifact.indic_text) == ("text", "ok", "Latin", False)
    assert artifact.chars == 32
    assert artifact.language is None
    assert artifact.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert artifact.size_bytes == path.stat().st_size
    assert artifact.untrusted is True and artifact.producer == "user_input"
    assert issues == []


async def test_markdown_kind_from_extension(tmp_path: Path) -> None:
    path = tmp_path / "sheet.md"
    path.write_text("| a | b |\n|---|---|\n| 1 | 2 |\n")
    artifact, _ = await inspect(path)
    assert artifact.kind == "markdown"


async def test_tamil_text_language_detected(tmp_path: Path) -> None:
    path = tmp_path / "ta.txt"
    path.write_text(TAMIL)
    artifact, _ = await inspect(path)
    assert (artifact.script, artifact.language, artifact.language_source, artifact.indic_text) == (
        "Tamil",
        "ta",
        "detected",
        True,
    )


async def test_devanagari_language_left_open(tmp_path: Path) -> None:
    path = tmp_path / "hi.txt"
    path.write_text(HINDI)
    artifact, _ = await inspect(path)
    assert artifact.script == "Devanagari"
    assert artifact.language is None and artifact.language_source is None


async def test_text_starting_with_bmp_magic_is_text(tmp_path: Path) -> None:
    path = tmp_path / "cars.txt"
    path.write_text("BMW cars are fast.\n")
    artifact, _ = await inspect(path)
    assert artifact.kind == "text"


async def test_kind_comes_from_content_not_extension(tmp_path: Path) -> None:
    path = tmp_path / "scan.txt"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    artifact, _ = await inspect(path)
    assert (artifact.kind, artifact.pages) == ("image", 1)


async def test_empty_file_failed(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_bytes(b"")
    artifact, issues = await inspect(path)
    assert artifact.step_status == "failed"
    assert [issue.code for issue in issues] == ["empty_input"]


async def test_whitespace_file_failed(tmp_path: Path) -> None:
    path = tmp_path / "blank.txt"
    path.write_text(" \n\t\n")
    artifact, issues = await inspect(path)
    assert artifact.step_status == "failed"
    assert [issue.code for issue in issues] == ["empty_input"]


async def test_binary_file_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "blob.bin"
    path.write_bytes(bytes(range(256)) * 4)
    artifact, issues = await inspect(path)
    assert (artifact.kind, artifact.step_status) == ("unknown", "failed")
    assert [issue.code for issue in issues] == ["unsupported_input"]


async def test_large_text_uses_sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preflight, "TEXT_SAMPLE_BYTES", 10)
    path = tmp_path / "long.txt"
    path.write_text("அ" * 20)
    artifact, issues = await inspect(path)
    assert artifact.kind == "text" and artifact.step_status == "ok"
    assert artifact.chars is None
    assert "larger than" in artifact.note
    assert artifact.script == "Tamil"
    assert issues == []


@needs_pdfinfo
async def test_pdf_pages(tmp_path: Path) -> None:
    artifact, issues = await inspect(make_pdf(tmp_path / "doc.pdf", 3))
    assert (artifact.kind, artifact.pages, artifact.step_status) == ("pdf", 3, "ok")
    assert issues == []


@needs_pdfinfo
async def test_broken_pdf_failed(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4\nnot really a pdf\n")
    artifact, issues = await inspect(path)
    assert (artifact.kind, artifact.step_status) == ("pdf", "failed")
    assert [issue.code for issue in issues] == ["unreadable_input"]


async def test_pdf_without_pdfinfo_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "pdfinfo" else real_which(name))
    artifact, issues = await inspect(make_pdf(tmp_path / "doc.pdf", 1))
    assert (artifact.kind, artifact.step_status, artifact.pages) == ("pdf", "warning", None)
    assert issues == []


@needs_ffmpeg
async def test_wav_duration(tmp_path: Path) -> None:
    artifact, issues = await inspect(make_wav(tmp_path / "clip.wav"))
    assert (artifact.kind, artifact.step_status) == ("audio", "ok")
    assert artifact.duration_s == pytest.approx(1.5, abs=0.1)
    assert issues == []


@needs_ffmpeg
async def test_video_with_audio(tmp_path: Path) -> None:
    path = tmp_path / "talk.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-f", "lavfi", "-i", "sine=duration=1",
        "-shortest", str(path),
    )  # fmt: skip
    artifact, issues = await inspect(path)
    assert (artifact.kind, artifact.step_status) == ("video", "ok")
    assert artifact.duration_s is not None
    assert issues == []


@needs_ffmpeg
async def test_video_without_audio_failed(tmp_path: Path) -> None:
    path = tmp_path / "silent.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", str(path))
    artifact, issues = await inspect(path)
    assert (artifact.kind, artifact.step_status) == ("video", "failed")
    assert artifact.warnings == ["no audio track"]
    assert [issue.code for issue in issues] == ["unsupported_input"]


@needs_ffmpeg
async def test_audio_without_ffprobe_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_wav(tmp_path / "clip.wav")
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "ffprobe" else real_which(name))
    artifact, issues = await inspect(path)
    assert (artifact.kind, artifact.step_status, artifact.duration_s) == ("audio", "warning", None)
    assert issues == []


def test_literals_mixed_task(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, task=f'Read aloud "{HINDI}" in Hindi, and translate “Good morning” too.')
    settings.literals_dir.mkdir(parents=True)
    ledger = Ledger()
    text, ids, needs_translation = preflight.extract_literals(settings.task, settings, ledger)
    assert text == 'Read aloud "[L1: 13 chars of Devanagari text]" in Hindi, and translate “Good morning” (L2) too.'
    assert ids == ["L1", "L2"] and needs_translation is False
    assert Path(ledger.get("L1").path).read_bytes() == HINDI.encode()
    assert Path(ledger.get("L2").path).read_text() == "Good morning"
    assert ledger.get("L1").indic_text is True and ledger.get("L2").indic_text is False


def test_unquoted_indic_phrase_kept_whole(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, task=f"Read aloud {HINDI} using a female voice.")
    settings.literals_dir.mkdir(parents=True)
    ledger = Ledger()
    text, ids, _ = preflight.extract_literals(settings.task, settings, ledger)
    assert text == "Read aloud [L1: 13 chars of Devanagari text] using a female voice."
    assert Path(ledger.get("L1").path).read_text() == HINDI
    assert ledger.get("L1").note == "Indian-language text from the task."


def test_literal_bytes_are_exact(tmp_path: Path) -> None:
    phrase = "  नमस्ते,\tदुनिया! "
    settings = settings_for(tmp_path, task=f"Speak «{phrase}» please")
    settings.literals_dir.mkdir(parents=True)
    ledger = Ledger()
    preflight.extract_literals(settings.task, settings, ledger)
    data = Path(ledger.get("L1").path).read_bytes()
    assert data == phrase.encode("utf-8")
    assert ledger.get("L1").sha256 == hashlib.sha256(data).hexdigest()


def test_blank_quotes_ignored(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, task='Translate " " into Tamil')
    settings.literals_dir.mkdir(parents=True)
    text, ids, _ = preflight.extract_literals(settings.task, settings, Ledger())
    assert ids == [] and text == settings.task


def test_plain_english_task_unchanged(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, task="Summarise this chapter in Tamil as a two-minute audio.")
    settings.literals_dir.mkdir(parents=True)
    assert preflight.extract_literals(settings.task, settings, Ledger()) == (settings.task, [], False)


async def test_run_preflight_registers_inputs(tmp_path: Path) -> None:
    root = tmp_path / "in"
    root.mkdir()
    (root / "a.txt").write_text("Hello")
    (root / "ta.txt").write_text(TAMIL)
    settings = settings_for(tmp_path, files=[root, tmp_path / "missing.wav"])
    ledger = Ledger()
    result = await preflight.run_preflight(settings, ledger)
    assert result.input_ids == ["f1", "f2"]
    assert [ledger.get(item).path for item in result.input_ids] == [str(root / "a.txt"), str(root / "ta.txt")]
    assert [issue.code for issue in result.issues] == ["missing_input"]
    assert settings.artifacts_dir.is_dir() and settings.literals_dir.is_dir()
    assert result.needs_translation is False and result.instruction_id is None


async def test_run_preflight_indic_task_saves_l0(tmp_path: Path) -> None:
    task = f'इस अध्याय का तमिल में सारांश बनाओ "{HINDI}"'
    settings = settings_for(tmp_path, task=task)
    ledger = Ledger()
    result = await preflight.run_preflight(settings, ledger)
    assert result.needs_translation is True and result.instruction_id == "L0"
    assert result.literal_ids == ["L1"]
    assert Path(ledger.get("L0").path).read_text() == task
    assert ledger.get("L0").note == preflight.L0_NOTE
    assert result.task_for_llm.startswith("[L0: the task is written in Devanagari script")
    assert "[L1: 13 chars of Devanagari text]" in result.task_for_llm
    assert not guards.contains_indic(result.task_for_llm)
    assert result.task == task


async def test_task_for_llm_never_contains_indic(tmp_path: Path) -> None:
    for index, task in enumerate([f"Speak {HINDI} and {TAMIL}", f"“{TAMIL}” in Tamil", HINDI]):
        settings = RunSettings.create(task, [], str(tmp_path / f"out{index}"))
        result = await preflight.run_preflight(settings, Ledger())
        assert not guards.contains_indic(result.task_for_llm)


async def test_missing_tools_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name in ("ffmpeg", "pdftoppm") else real_which(name))
    result = await preflight.run_preflight(settings_for(tmp_path), Ledger())
    assert result.missing_tools == ["ffmpeg", "pdftoppm"]


async def test_mostly_english_file_keeps_latin_script(tmp_path: Path) -> None:
    path = tmp_path / "mixed.txt"
    path.write_text("This chapter explains how models generalize. यह एक उदाहरण है। We now discuss overfitting.")
    artifact, _ = await inspect(path)
    assert (artifact.script, artifact.indic_text, artifact.language) == ("Latin", True, None)


def test_mixed_quote_marker_names_indic(tmp_path: Path) -> None:
    quote = "Good morning everyone, नमस्ते"
    settings = settings_for(tmp_path, task=f'Read "{quote}" aloud')
    settings.literals_dir.mkdir(parents=True)
    text, _, _ = preflight.extract_literals(settings.task, settings, Ledger())
    assert text == f'Read "[L1: {len(quote)} chars of Indic text]" aloud'
