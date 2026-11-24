"""Pipeline stage resume: reuse artifacts before ``--from``, re-run from there.

Stages (first to run = ``--from`` value):

  pages → ocr → layout → assemble → translate → narrate → speak → qa

Omit ``--from`` for a full new run.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bodhan_lesson.ocr import list_page_images, load_existing_ocr_pages

# Ordered pipeline. ``--from X`` reuses everything strictly before X.
STAGES: tuple[str, ...] = (
    "pages",
    "ocr",
    "layout",
    "assemble",
    "translate",
    "narrate",
    "speak",
    "qa",
)

# Public choices for argparse (pages is only for completeness; usual starts are ocr+).
FROM_CHOICES: tuple[str, ...] = STAGES


def stage_index(stage: str) -> int:
    try:
        return STAGES.index(stage)
    except ValueError as exc:
        raise ValueError(
            f"Unknown stage {stage!r}. Choose one of: {', '.join(STAGES)}"
        ) from exc


def should_run(from_stage: str | None, stage: str) -> bool:
    """True if ``stage`` must execute (at or after ``from_stage``)."""
    if from_stage is None:
        return True
    return stage_index(stage) >= stage_index(from_stage)


def reuse_plan(from_stage: str | None) -> dict[str, str]:
    """Map each stage to 'reuse' or 'run' for logging."""
    return {
        stage: ("run" if should_run(from_stage, stage) else "reuse")
        for stage in STAGES
    }


def require_page_images(pages_dir: Path) -> list[Path]:
    images = list_page_images(pages_dir)
    if not images:
        raise FileNotFoundError(
            f"No page_* images under {pages_dir}. "
            "Run a full pipeline once (no --from), or --from pages."
        )
    return images


def load_layout_pages(pages_dir: Path, ocr_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild layout page rows from saved ``page_*.clean.json``."""
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for ocr in ocr_pages:
        if ocr.get("status") != "ok":
            rows.append(
                {
                    "page": ocr.get("page"),
                    "stem": ocr.get("stem"),
                    "status": "error",
                    "repair": "skipped",
                    "error": ocr.get("error") or "ocr_not_ok",
                    "reused": True,
                }
            )
            continue
        stem = ocr.get("stem") or ""
        clean_path = pages_dir / f"{stem}.clean.json"
        if not clean_path.is_file():
            missing.append(stem)
            continue
        data = json.loads(clean_path.read_text(encoding="utf-8"))
        rows.append(
            {
                "page": ocr.get("page"),
                "stem": stem,
                "status": "ok",
                "repair": data.get("repair") or "skipped",
                "multi_column": bool(data.get("multi_column")),
                "clean_json_path": str(clean_path),
                "md_path": str(pages_dir / f"{stem}.md"),
                "llm_usage": data.get("llm_usage") or {},
                "error": None,
                "latency_s": 0.0,
                "reused": True,
            }
        )
    if missing:
        raise FileNotFoundError(
            f"Missing clean.json for: {', '.join(missing)}. "
            "Run with --from layout (or earlier) once first."
        )
    if not rows:
        raise FileNotFoundError(f"No layout artifacts under {pages_dir}")
    return rows


def load_assembly(source_dir: Path) -> dict[str, Any]:
    """Load ``assembly.json`` + require ``lesson.src.md``."""
    lesson_path = source_dir / "lesson.src.md"
    assembly_path = source_dir / "assembly.json"
    if not lesson_path.is_file():
        raise FileNotFoundError(
            f"Missing {lesson_path}. Run with --from assemble (or earlier) once first."
        )
    if assembly_path.is_file():
        data = json.loads(assembly_path.read_text(encoding="utf-8"))
        data["lesson_path"] = str(lesson_path)
        data["reused"] = True
        return data
    return {
        "status": "ok",
        "lesson_path": str(lesson_path),
        "assembly_path": str(assembly_path),
        "ok": 1,
        "failed": 0,
        "reused": True,
    }


def _load_lang_artifacts(
    langs: list[str],
    *,
    missing_hint: str,
    build_row: Callable[[str], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """Build per-lang reuse rows; ``build_row`` returns None when artifacts are missing."""
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for lang in langs:
        row = build_row(lang)
        if row is None:
            missing.append(lang)
            continue
        rows.append(row)
    if missing:
        raise FileNotFoundError(
            f"{missing_hint}: {', '.join(missing)}. "
            "Run an earlier --from stage (or a full run) once first."
        )
    return rows


def load_translations(out_dir: Path, langs: list[str]) -> list[dict[str, Any]]:
    def _row(lang: str) -> dict[str, Any] | None:
        path = out_dir / lang / f"lesson.{lang}.md"
        if not path.is_file():
            return None
        return {
            "lang": lang,
            "status": "ok",
            "path": str(path),
            "pivot": False,
            "chunks": 0,
            "usage": {},
            "structure_ok": True,
            "structure_issues": [],
            "latency_s": 0.0,
            "error": None,
            "reused": True,
        }

    return _load_lang_artifacts(
        langs,
        missing_hint="Missing lesson.{lang}.md for",
        build_row=_row,
    )


def load_narrations(out_dir: Path, langs: list[str]) -> list[dict[str, Any]]:
    def _row(lang: str) -> dict[str, Any] | None:
        path = out_dir / lang / f"narration_script.{lang}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        sections = data.get("sections") or []
        ok = sum(1 for s in sections if s.get("status") == "ok")
        return {
            "lang": lang,
            "status": "ok",
            "path": str(path),
            "ok": ok,
            "skipped": len(sections) - ok,
            "llm_usage": {},
            "translate_usage": {},
            "latency_s": 0.0,
            "error": None,
            "reused": True,
        }

    return _load_lang_artifacts(
        langs,
        missing_hint="Missing narration_script.{lang}.json for",
        build_row=_row,
    )


def load_speaks(out_dir: Path, langs: list[str]) -> list[dict[str, Any]]:
    def _row(lang: str) -> dict[str, Any] | None:
        mp3 = out_dir / lang / f"lesson.{lang}.mp3"
        script = out_dir / lang / f"narration_script.{lang}.json"
        if not mp3.is_file() or not script.is_file():
            return None
        data = json.loads(script.read_text(encoding="utf-8"))
        return {
            "lang": lang,
            "status": "ok",
            "lesson_mp3": str(mp3),
            "script_path": str(script),
            "voice": data.get("voice"),
            "sections_ok": sum(
                1
                for s in (data.get("sections") or [])
                if s.get("speak_status") == "ok" or s.get("audio_path")
            ),
            "sections_skipped": 0,
            "sections_failed": 0,
            "retries": 0,
            "latency_s": 0.0,
            "error": None,
            "ai_generated": True,
            "reused": True,
        }

    return _load_lang_artifacts(
        langs,
        missing_hint="Missing lesson.{lang}.mp3 (or script) for",
        build_row=_row,
    )


def load_qas(out_dir: Path, langs: list[str]) -> list[dict[str, Any]]:
    def _row(lang: str) -> dict[str, Any] | None:
        script = out_dir / lang / f"narration_script.{lang}.json"
        if not script.is_file():
            return None
        data = json.loads(script.read_text(encoding="utf-8"))
        summary = data.get("qa_summary") or {}
        if not summary:
            return None
        failed = int(summary.get("failed") or 0)
        ok = int(summary.get("ok") or 0)
        status = "ok" if failed == 0 and ok > 0 else ("partial" if ok else "error")
        return {
            "lang": lang,
            "status": status,
            "ok": ok,
            "failed": failed,
            "skipped": int(summary.get("skipped") or 0),
            "retries": int(summary.get("retries") or 0),
            "mean_cer": summary.get("mean_cer"),
            "loudness": summary.get("loudness") or {},
            "lesson_mp3": data.get("lesson_mp3"),
            "script_path": str(script),
            "latency_s": 0.0,
            "error": None if status == "ok" else "reused_qa_not_ok",
            "reused": True,
        }

    return _load_lang_artifacts(
        langs,
        missing_hint="Missing qa_summary in narration scripts for",
        build_row=_row,
    )

def ensure_prerequisites(from_stage: str, pages_dir: Path, out_dir: Path, langs: list[str]) -> None:
    """Fail fast with a clear message if required artifacts for --from are missing."""
    source_dir = out_dir / "source"
    # Need everything before from_stage.
    if should_run(from_stage, "pages"):
        return
    require_page_images(pages_dir)
    if should_run(from_stage, "ocr"):
        return
    # layout+ needs OCR
    ocr_files = list(pages_dir.glob("page_*.ocr.json"))
    if not ocr_files:
        raise FileNotFoundError(
            f"No page_*.ocr.json under {pages_dir}. Run without --from (or --from ocr) once first."
        )
    if should_run(from_stage, "layout"):
        return
    if should_run(from_stage, "assemble"):
        return
    if not (source_dir / "lesson.src.md").is_file() and stage_index(from_stage) > stage_index(
        "assemble"
    ):
        # translate+ needs assemble
        pass
    if stage_index(from_stage) >= stage_index("translate"):
        if stage_index(from_stage) > stage_index("assemble"):
            if not (source_dir / "lesson.src.md").is_file():
                raise FileNotFoundError(
                    f"Missing {source_dir / 'lesson.src.md'}. Run --from assemble once first."
                )
    if stage_index(from_stage) > stage_index("translate"):
        for lang in langs:
            if not (out_dir / lang / f"lesson.{lang}.md").is_file():
                raise FileNotFoundError(
                    f"Missing lesson.{lang}.md. Run --from translate once first."
                )
    if stage_index(from_stage) > stage_index("narrate"):
        for lang in langs:
            if not (out_dir / lang / f"narration_script.{lang}.json").is_file():
                raise FileNotFoundError(
                    f"Missing narration_script.{lang}.json. Run --from narrate once first."
                )
    if stage_index(from_stage) > stage_index("speak"):
        for lang in langs:
            if not (out_dir / lang / f"lesson.{lang}.mp3").is_file():
                raise FileNotFoundError(
                    f"Missing lesson.{lang}.mp3. Run --from speak once first."
                )
