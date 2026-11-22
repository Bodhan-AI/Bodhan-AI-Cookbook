"""Integration tests: real filesystem CLI runs, OCR HTTP mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from bodhan_lesson.cli import main


def _fake_ocr_ok(pages_dir: Path, **kwargs):  # noqa: ANN003
    images = [
        p
        for p in sorted(pages_dir.glob("page_*.*"))
        if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
    ]
    rows = []
    for img in images:
        md = pages_dir / f"{img.stem}.md"
        ocr_json = pages_dir / f"{img.stem}.ocr.json"
        md.write_text("mock markdown\n", encoding="utf-8")
        ocr_json.write_text(
            json.dumps(
                {
                    "page_image": img.name,
                    "content": "mock markdown",
                    "blocks": [
                        {
                            "order": 0,
                            "label": "Paragraph",
                            "type": "Text",
                            "bbox_xyxy": [10, 10, 200, 40],
                            "conf": 0.95,
                            "text": "mock markdown",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        rows.append(
            {
                "page": img.name,
                "stem": img.stem,
                "status": "ok",
                "block_count": 1,
                "md_path": str(md),
                "ocr_json_path": str(ocr_json),
                "usage": {},
                "error": None,
                "latency_s": 0.01,
                "retries": 0,
            }
        )
    return rows


def _fake_translate(lesson_path, out_dir, langs, **kwargs):  # noqa: ANN001
    text = Path(lesson_path).read_text(encoding="utf-8")
    rows = []
    for lang in langs:
        path = Path(out_dir) / lang / f"lesson.{lang}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        rows.append(
            {
                "lang": lang,
                "status": "ok",
                "path": str(path),
                "pivot": False,
                "chunks": 1,
                "usage": {},
                "structure_ok": True,
                "structure_issues": [],
                "latency_s": 0.01,
                "error": None,
            }
        )
    return rows


def _fake_speak(out_dir, narrations, **kwargs):  # noqa: ANN001
    rows = []
    for narr in narrations:
        lang = narr.get("lang")
        if narr.get("status") != "ok":
            rows.append(
                {
                    "lang": lang,
                    "status": "skipped",
                    "lesson_mp3": None,
                    "error": narr.get("error"),
                    "latency_s": 0.0,
                    "retries": 0,
                    "ai_generated": True,
                }
            )
            continue
        mp3 = Path(out_dir) / lang / f"lesson.{lang}.mp3"
        mp3.parent.mkdir(parents=True, exist_ok=True)
        mp3.write_bytes(b"ID3fake")
        rows.append(
            {
                "lang": lang,
                "status": "ok",
                "lesson_mp3": str(mp3),
                "script_path": str(Path(out_dir) / lang / f"narration_script.{lang}.json"),
                "voice": "Kavya",
                "sections_ok": 1,
                "sections_skipped": 0,
                "sections_failed": 0,
                "retries": 0,
                "latency_s": 0.01,
                "error": None,
                "ai_generated": True,
            }
        )
    return rows


def _fake_qa(out_dir, speaks, **kwargs):  # noqa: ANN001
    rows = []
    for spk in speaks:
        lang = spk.get("lang")
        if spk.get("status") not in {"ok", "partial"}:
            rows.append(
                {
                    "lang": lang,
                    "status": "skipped",
                    "ok": 0,
                    "failed": 0,
                    "skipped": 0,
                    "retries": 0,
                    "mean_cer": None,
                    "loudness": {"status": "skipped"},
                    "latency_s": 0.0,
                    "error": spk.get("error"),
                }
            )
            continue
        rows.append(
            {
                "lang": lang,
                "status": "ok",
                "ok": 1,
                "failed": 0,
                "skipped": 0,
                "retries": 0,
                "mean_cer": 0.04,
                "loudness": {"status": "skipped", "reason": "ffmpeg_missing"},
                "lesson_mp3": spk.get("lesson_mp3"),
                "latency_s": 0.01,
                "error": None,
            }
        )
    return rows


def test_cli_phase2_from_image_folder(image_folder: Path, tmp_path: Path) -> None:
    out = tmp_path / "out_images"
    with patch("bodhan_lesson.cli.run_ocr_on_pages", side_effect=_fake_ocr_ok):
        with patch("bodhan_lesson.cli.translate_lesson_file", side_effect=_fake_translate):
            with patch(
                "bodhan_lesson.cli.speak_narrated_lessons", side_effect=_fake_speak
            ):
                with patch(
                    "bodhan_lesson.cli.qa_spoken_lessons", side_effect=_fake_qa
                ):
                    code = main(
                        [
                            "--input", str(image_folder),
                            "--langs", "hi,ta",
                            "--out", str(out),
                            "--source-lang", "en",
                        ]
                    )
    assert code == 0
    pages = sorted(
        p
        for p in (out / "source" / "pages").glob("page_*")
        if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
    )
    assert len(pages) == 2
    assert (out / "source" / "pages" / "page_001.md").is_file()
    assert (out / "hi" / "narration").is_dir()
    assert (out / "ta" / "narration").is_dir()
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["page_count"] == 2
    assert report["langs"] == ["hi", "ta"]
    assert report["phase"] == 7
    assert report["ocr"]["ok"] == 2
    assert report["layout"]["ok"] == 2
    assert report["assembly"]["status"] == "ok"
    assert report["translations"]["ok"] == 2
    assert report["narrations"]["ok"] == 2
    assert report["speaks"]["ok"] == 2
    assert report["qa"]["ok"] == 2
    assert (out / "source" / "pages" / "page_001.clean.json").is_file()
    assert (out / "source" / "lesson.src.md").is_file()
    assert (out / "source" / "assembly.json").is_file()
    assert (out / "hi" / "lesson.hi.md").is_file()
    assert (out / "hi" / "narration_script.hi.json").is_file()
    assert (out / "hi" / "lesson.hi.mp3").is_file()


def test_cli_phase2_from_pdf(multi_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out_pdf"
    with patch("bodhan_lesson.cli.run_ocr_on_pages", side_effect=_fake_ocr_ok):
        with patch("bodhan_lesson.cli.translate_lesson_file", side_effect=_fake_translate):
            with patch(
                "bodhan_lesson.cli.speak_narrated_lessons", side_effect=_fake_speak
            ):
                with patch(
                    "bodhan_lesson.cli.qa_spoken_lessons", side_effect=_fake_qa
                ):
                    code = main(
                        [
                            "--input", str(multi_pdf),
                            "--langs", "bn",
                            "--out", str(out),
                            "--source-lang", "en",
                        ]
                    )
    assert code == 0
    pages = sorted((out / "source" / "pages").glob("page_*.png"))
    assert len(pages) == 3
    assert all(p.stat().st_size > 0 for p in pages)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["page_count"] == 3
    assert report["phase"] == 7
    assert report["layout"]["ok"] == 3
    assert report["assembly"]["status"] == "ok"
    assert report["languages"]["bn"]["status"] == "qa_ok"
    assert (out / "bn" / "narration_script.bn.json").is_file()
    assert (out / "bn" / "lesson.bn.mp3").is_file()


def test_cli_output_layout_contract(tiny_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "layout"
    with patch("bodhan_lesson.cli.run_ocr_on_pages", side_effect=_fake_ocr_ok):
        with patch("bodhan_lesson.cli.translate_lesson_file", side_effect=_fake_translate):
            with patch(
                "bodhan_lesson.cli.speak_narrated_lessons", side_effect=_fake_speak
            ):
                with patch(
                    "bodhan_lesson.cli.qa_spoken_lessons", side_effect=_fake_qa
                ):
                    assert (
                        main(
                            [
                                "--input", str(tiny_pdf),
                                "--langs", "hi",
                                "--out", str(out),
                                "--source-lang", "en",
                            ]
                        )
                        == 0
                    )
    assert (out / "source" / "pages" / "page_001.png").is_file()
    assert (out / "source" / "pages" / "page_001.md").is_file()
    assert (out / "source" / "pages" / "page_001.clean.json").is_file()
    assert (out / "source" / "lesson.src.md").is_file()
    assert (out / "hi" / "lesson.hi.md").is_file()
    assert (out / "hi" / "narration_script.hi.json").is_file()
    assert (out / "hi" / "lesson.hi.mp3").is_file()
    assert (out / "hi" / "narration").is_dir()
    assert (out / "report.json").is_file()
