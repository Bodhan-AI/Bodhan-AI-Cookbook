"""Conditional LLM reading-order repair for OCR blocks.

Deterministic cleanup always runs. LLM reorder runs only when a simple
multi-column heuristic fires. Invalid LLM output → keep cleanup order.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bodhan_lesson.layout_cleanup import (
    block_id,
    cleanup_blocks,
    collect_chapter_titles,
    normalize_preview,
)
from bodhan_lesson.llm_client import chat_json

# Labels that count as "text" for the side-by-side column check.
TEXTISH_LABELS = {
    "Paragraph",
    "Section-title",
    "Chapter-title",
    "Title",
    "List-item",
    "Equation",
    "Image-caption",
    "Caption",
    "Footnote",
    "Text",
}

# Minimum horizontal gap (pixels at render DPI) between side-by-side boxes.
_MIN_X_GAP = 40.0
# Overlap / min-height ratio to treat two boxes as same band.
_MIN_Y_OVERLAP_RATIO = 0.25
_PREVIEW_LEN = 100


def _bbox(block: dict[str, Any]) -> tuple[float, float, float, float] | None:
    box = block.get("bbox_xyxy")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _y_overlap_ratio(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    _, ay0, _, ay1 = a
    _, by0, _, by1 = b
    overlap = max(0.0, min(ay1, by1) - max(ay0, by0))
    shorter = min(ay1 - ay0, by1 - by0)
    if shorter <= 0:
        return 0.0
    return overlap / shorter


def _x_gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, _, ax1, _ = a
    bx0, _, bx1, _ = b
    if ax1 <= bx0:
        return bx0 - ax1
    if bx1 <= ax0:
        return ax0 - bx1
    return 0.0  # horizontal overlap


def suggests_multi_column(blocks: list[dict[str, Any]]) -> bool:
    """True if two textish boxes sit side-by-side in the same vertical band."""
    text_blocks: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []
    for block in blocks:
        label = block.get("label") or ""
        if label not in TEXTISH_LABELS:
            continue
        box = _bbox(block)
        if box is None:
            continue
        text_blocks.append((block, box))

    for i in range(len(text_blocks)):
        _, box_a = text_blocks[i]
        for j in range(i + 1, len(text_blocks)):
            _, box_b = text_blocks[j]
            if _y_overlap_ratio(box_a, box_b) < _MIN_Y_OVERLAP_RATIO:
                continue
            if _x_gap(box_a, box_b) >= _MIN_X_GAP:
                return True
    return False


def _repair_prompt(kept: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = [
        {
            "id": b["id"],
            "label": b.get("label"),
            "bbox_xyxy": b.get("bbox_xyxy"),
            "conf": b.get("conf"),
            "preview": normalize_preview(b.get("text") or "", max_len=_PREVIEW_LEN),
        }
        for b in kept
    ]
    system = (
        "You fix OCR reading order for a textbook page. "
        "Reply with JSON only. Never invent or rewrite text. Use only the given block ids. "
        'Schema: {"ordered_ids":["..."],"drop_ids":["..."],"doubtful_ids":["..."]}. '
        "Rules: "
        "(1) Order for normal reading: top-to-bottom; within the same band, left-to-right. "
        "(2) When blocks belong to one figure or illustration group, put the overall caption "
        'first (names the whole figure, often starts with "Fig." / "Figure"), then part labels '
        "such as (i), (ii), (a), (b) in order. "
        "(3) drop_ids: clear junk only — empty blocks with no useful text, leftover codes, ads, "
        "noise. Do not drop real body text or real captions. "
        "(4) doubtful_ids: low confidence or unclear placement; every input id must appear in "
        "exactly one of ordered_ids or drop_ids."
    )
    user = (
        "These blocks may be multi-column or scrambled. "
        "Return reading order as ordered_ids. Apply the rules above.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def validate_repair_schema(
    data: dict[str, Any],
    allowed_ids: set[str],
) -> dict[str, list[str]]:
    """Validate LLM JSON; raise ValueError if unusable."""
    if not isinstance(data, dict):
        raise ValueError("repair result must be an object")

    ordered = data.get("ordered_ids")
    drop_ids = data.get("drop_ids") or []
    doubtful = data.get("doubtful_ids") or []

    if not isinstance(ordered, list) or not ordered:
        raise ValueError("ordered_ids must be a non-empty list")
    if not isinstance(drop_ids, list) or not isinstance(doubtful, list):
        raise ValueError("drop_ids and doubtful_ids must be lists")

    ordered_s = [str(x) for x in ordered]
    drop_s = [str(x) for x in drop_ids]
    doubtful_s = [str(x) for x in doubtful]

    if len(ordered_s) != len(set(ordered_s)):
        raise ValueError("ordered_ids has duplicates")
    if any(i not in allowed_ids for i in ordered_s):
        raise ValueError("ordered_ids contains unknown id")
    if any(i not in allowed_ids for i in drop_s):
        raise ValueError("drop_ids contains unknown id")
    if any(i not in allowed_ids for i in doubtful_s):
        raise ValueError("doubtful_ids contains unknown id")
    if set(ordered_s) & set(drop_s):
        raise ValueError("id appears in both ordered_ids and drop_ids")

    # Every kept id should be either ordered or dropped.
    covered = set(ordered_s) | set(drop_s)
    if covered != allowed_ids:
        missing = allowed_ids - covered
        extra = covered - allowed_ids
        raise ValueError(f"id coverage mismatch missing={missing} extra={extra}")

    return {
        "ordered_ids": ordered_s,
        "drop_ids": drop_s,
        "doubtful_ids": doubtful_s,
    }


def apply_repair(
    kept: list[dict[str, Any]],
    *,
    ordered_ids: list[str],
    drop_ids: list[str],
    doubtful_ids: list[str],
) -> list[dict[str, Any]]:
    """Reorder kept blocks by id; drop listed ids; merge doubtful flags."""
    by_id = {b["id"]: dict(b) for b in kept}
    drop_set = set(drop_ids)
    doubt_set = set(doubtful_ids)
    result: list[dict[str, Any]] = []
    for bid in ordered_ids:
        if bid in drop_set:
            continue
        block = by_id[bid]
        flags = list(block.get("flags") or [])
        if bid in doubt_set and "doubtful" not in flags:
            flags.append("doubtful")
        block["flags"] = flags
        result.append(block)
    return result


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    """Join kept block texts for cleaned page markdown (OCR text only)."""
    parts: list[str] = []
    for block in blocks:
        text = (block.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts) + ("\n" if parts else "")


def repair_page_blocks(
    blocks: list[dict[str, Any]],
    *,
    page_index: int = 0,
    chapter_titles: set[str] | None = None,
    use_llm: bool = True,
    llm_api_key: str | None = None,
) -> dict[str, Any]:
    """Cleanup + optional LLM reorder. Returns clean structure + status."""
    cleaned = cleanup_blocks(
        blocks, page_index=page_index, chapter_titles=chapter_titles
    )
    kept = cleaned["kept"]
    multi = suggests_multi_column(kept)

    repair_status = "skipped"
    repair_error: str | None = None
    llm_drop: list[dict[str, Any]] = []
    final_blocks = kept
    doubtful_ids = list(cleaned["doubtful_ids"])
    llm_usage: dict[str, int] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }

    if multi and use_llm and kept:
        allowed = {b["id"] for b in kept}
        try:
            raw, llm_usage = chat_json(_repair_prompt(kept), api_key=llm_api_key)
            parsed = validate_repair_schema(raw, allowed)
            final_blocks = apply_repair(
                kept,
                ordered_ids=parsed["ordered_ids"],
                drop_ids=parsed["drop_ids"],
                doubtful_ids=parsed["doubtful_ids"],
            )
            for bid in parsed["drop_ids"]:
                llm_drop.append({"id": bid, "reason": "llm_drop"})
            for bid in parsed["doubtful_ids"]:
                if bid not in doubtful_ids:
                    doubtful_ids.append(bid)
            repair_status = "llm"
        except Exception as exc:  # noqa: BLE001 — fallback to OCR cleanup order
            repair_status = "fallback"
            repair_error = str(exc)
            final_blocks = kept
    elif multi and not use_llm:
        repair_status = "fallback"
        repair_error = "LLM disabled"
    elif not multi:
        repair_status = "skipped"

    return {
        "blocks": final_blocks,
        "dropped": cleaned["dropped"] + llm_drop,
        "doubtful_ids": doubtful_ids,
        "multi_column": multi,
        "repair": repair_status,
        "repair_error": repair_error,
        "llm_usage": llm_usage,
        "low_conf_threshold": cleaned["low_conf_threshold"],
        "content": blocks_to_markdown(final_blocks),
    }


def load_ocr_artifact(ocr_json_path: Path) -> dict[str, Any]:
    data = json.loads(ocr_json_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"bad OCR artifact: {ocr_json_path}")
    return data


def save_clean_artifacts(
    pages_dir: Path,
    stem: str,
    *,
    page_image: str,
    result: dict[str, Any],
) -> dict[str, str]:
    """Write page_NNN.clean.json and replace page_NNN.md with cleaned text."""
    clean_path = pages_dir / f"{stem}.clean.json"
    md_path = pages_dir / f"{stem}.md"

    artifact = {
        "page_image": page_image,
        "multi_column": result["multi_column"],
        "repair": result["repair"],
        "repair_error": result.get("repair_error"),
        "llm_usage": result.get("llm_usage")
        or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "low_conf_threshold": result["low_conf_threshold"],
        "doubtful_ids": result["doubtful_ids"],
        "dropped": result["dropped"],
        "blocks": result["blocks"],
        "content": result["content"],
    }
    clean_path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(result["content"], encoding="utf-8")
    return {"clean_json_path": str(clean_path), "md_path": str(md_path)}


def run_layout_on_pages(
    pages_dir: Path,
    ocr_pages: list[dict[str, Any]],
    *,
    use_llm: bool = True,
    llm_api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Cleanup/repair each OCR-ok page. Failures are isolated per page."""
    chapter_titles: set[str] = set()
    results: list[dict[str, Any]] = []

    # First successful page seeds running-header titles.
    ordered = sorted(
        [row for row in ocr_pages if row.get("status") == "ok"],
        key=lambda row: row.get("stem") or row.get("page") or "",
    )

    for page_index, row in enumerate(ordered):
        stem = row.get("stem") or Path(str(row.get("page", ""))).stem
        ocr_json_path = row.get("ocr_json_path")
        started = time.perf_counter()
        try:
            if not ocr_json_path:
                raise FileNotFoundError(f"missing ocr_json_path for {stem}")
            artifact = load_ocr_artifact(Path(ocr_json_path))
            blocks = artifact.get("blocks") or []
            if not isinstance(blocks, list):
                raise ValueError("blocks must be a list")

            if page_index == 0:
                chapter_titles = collect_chapter_titles(blocks)

            repaired = repair_page_blocks(
                blocks,
                page_index=page_index,
                chapter_titles=chapter_titles,
                use_llm=use_llm,
                llm_api_key=llm_api_key,
            )
            paths = save_clean_artifacts(
                pages_dir,
                stem,
                page_image=artifact.get("page_image") or row.get("page") or f"{stem}.png",
                result=repaired,
            )
            results.append(
                {
                    "page": row.get("page"),
                    "stem": stem,
                    "status": "ok",
                    "multi_column": repaired["multi_column"],
                    "repair": repaired["repair"],
                    "repair_error": repaired.get("repair_error"),
                    "llm_usage": repaired.get("llm_usage")
                    or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "kept": len(repaired["blocks"]),
                    "dropped": len(repaired["dropped"]),
                    "doubtful": len(repaired["doubtful_ids"]),
                    "clean_json_path": paths["clean_json_path"],
                    "md_path": paths["md_path"],
                    "latency_s": round(time.perf_counter() - started, 3),
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001 — isolate per page
            results.append(
                {
                    "page": row.get("page"),
                    "stem": stem,
                    "status": "error",
                    "multi_column": False,
                    "repair": "error",
                    "repair_error": str(exc),
                    "llm_usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                    "kept": 0,
                    "dropped": 0,
                    "doubtful": 0,
                    "clean_json_path": None,
                    "md_path": None,
                    "latency_s": round(time.perf_counter() - started, 3),
                    "error": str(exc),
                }
            )

    # Preserve original OCR order in report (match ocr_pages sequence).
    by_stem = {r["stem"]: r for r in results}
    ordered_out: list[dict[str, Any]] = []
    for row in ocr_pages:
        if row.get("status") != "ok":
            continue
        stem = row.get("stem") or Path(str(row.get("page", ""))).stem
        if stem in by_stem:
            ordered_out.append(by_stem[stem])
    return ordered_out
