"""Canonical page order and conservative, one-for-one OCR repair."""

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from hashlib import sha256
from io import BytesIO
from math import ceil, floor
from pathlib import Path

from PIL import Image

from ..inference import ProviderError
from ..models import BlockKind, OCRBlock, OCRPage, Page, PreprocessedPage
from ..report import AttemptLog
from .extraction import CONTENT_FLAGS, ocr_page, write_ocr_page
from .inputs import png_bytes
from .preprocess import map_box_to_original
from .provider import OCRResponse

MIN_CONFIDENCE = 0.70
NON_LESSON = {"figure", "header", "footer", "page_number", "advertisement"}
TEXT_KINDS = {"heading", "paragraph", "caption", "footnote", "sidebar"}
ORDER_FIELD = "ordered_block_ids"
OrderProposal = Callable[[str, tuple[OCRBlock, ...]], str]
ReadOCR = Callable[[str, bytes], OCRResponse]


@dataclass(frozen=True)
class ReadingOrder:
    ids: tuple[str, ...]
    method: str
    reason: str
    fallback: bool = False


@dataclass(frozen=True)
class CropCandidate:
    kind: BlockKind
    text: str
    confidence: float | None
    issues: tuple[str, ...]


@dataclass(frozen=True)
class RepairDecision:
    block_id: str
    issues: tuple[str, ...]
    original_text: str
    disposition: str
    reason: str
    replacement_text: str | None = None
    replacement_confidence: float | None = None
    original_crop_box: tuple[int, int, int, int] | None = None
    attempt_ids: tuple[str, ...] = ()
    crop_candidates: tuple[CropCandidate, ...] = ()


@dataclass(frozen=True)
class PageLayout:
    page_id: str
    status: str
    before_ids: tuple[str, ...]
    after_ids: tuple[str, ...]
    method: str
    reason: str
    fallback: bool
    quarantined_ids: tuple[str, ...]
    order_attempt_ids: tuple[str, ...]
    repairs: tuple[RepairDecision, ...]


def validate_order(proposed: object, expected: tuple[str, ...]) -> bool:
    """Require a JSON array containing exactly one occurrence of each existing ID."""
    return (
        isinstance(proposed, list)
        and len(proposed) == len(expected)
        and all(isinstance(block_id, str) for block_id in proposed)
        and len(set(proposed)) == len(expected)
        and set(proposed) == set(expected)
    )


def _vertical_order(blocks: list[OCRBlock]) -> list[OCRBlock] | None:
    ordered = sorted(blocks, key=lambda block: (block.bbox[1], block.bbox[0]))
    for first, second in zip(ordered, ordered[1:]):
        # Small detector overlaps along adjacent lines do not imply a second column.
        tolerance = 0.15 * min(first.bbox[3] - first.bbox[1], second.bbox[3] - second.bbox[1])
        if first.bbox[3] - second.bbox[1] > tolerance:
            return None
    return ordered


def _order_band(blocks: list[OCRBlock], content_width: float) -> list[OCRBlock] | None:
    columns: list[list[OCRBlock]] = []
    right_edge = float("-inf")
    for block in sorted(blocks, key=lambda block: block.bbox[0]):
        if not columns or block.bbox[0] - right_edge >= 0.02 * content_width:
            columns.append([])
        columns[-1].append(block)
        right_edge = max(right_edge, block.bbox[2])
    if len(columns) > 1:
        ranges = sorted((min(block.bbox[1] for block in column),
                         max(block.bbox[3] for block in column)) for column in columns)
        if all(first[1] <= second[0] for first, second in zip(ranges, ranges[1:])):
            return _vertical_order(blocks)
    ordered = []
    for column in columns:
        vertical = _vertical_order(column)
        if vertical is None:
            return None
        ordered.extend(vertical)
    return ordered


def _geometry_order(blocks: tuple[OCRBlock, ...]) -> tuple[str, ...] | None:
    headers = [block for block in blocks if block.kind == "header"]
    footers = [block for block in blocks if block.kind == "footer"]
    body = [block for block in blocks if block.kind not in {"header", "footer"}]
    if not body:
        return tuple(block.id for block in (*headers, *footers))
    left = min(block.bbox[0] for block in body)
    right = max(block.bbox[2] for block in body)
    width = right - left
    pending = sorted(body, key=lambda block: block.bbox[1])
    ordered = list(headers)
    band = []
    last_heading_bottom = None
    for block in pending:
        if last_heading_bottom is not None and block.bbox[1] < last_heading_bottom:
            return None
        spans_width = block.bbox[0] <= left + 0.15 * width and block.bbox[2] >= right - 0.15 * width
        if block.kind != "heading" or not spans_width:
            band.append(block)
            continue
        if any(other.bbox[3] > block.bbox[1] for other in band):
            return None
        vertical = _order_band(band, width)
        if vertical is None:
            return None
        ordered.extend((*vertical, block))
        band = []
        last_heading_bottom = block.bbox[3]
    vertical = _order_band(band, width)
    if vertical is None:
        return None
    ordered.extend((*vertical, *footers))
    return tuple(block.id for block in ordered)


def order_blocks(page: OCRPage, propose: OrderProposal) -> ReadingOrder:
    before = page.ordered_block_ids
    if page.status == "failed":
        return ReadingOrder(before, "unavailable", page.reason or "OCR failed.")
    if any(block.bbox is None for block in page.blocks):
        return ReadingOrder(before, "provider", "Some blocks have no geometry.", fallback=True)
    if len(page.blocks) <= 1:
        return ReadingOrder(before, "trivial", "At most one OCR block.")
    geometry = _geometry_order(page.blocks)
    if geometry is not None:
        return ReadingOrder(geometry, "geometry", "Read columns left to right within heading bands.")

    try:
        result = json.loads(propose(page.page_id, page.blocks))
    except ProviderError as error:
        return ReadingOrder(before, "provider", str(error), fallback=True)
    except (ValueError, RecursionError):
        return ReadingOrder(before, "provider", "Layout judgment returned invalid JSON.", fallback=True)
    ids = result.get(ORDER_FIELD) if isinstance(result, dict) else None
    if not validate_order(ids, before):
        return ReadingOrder(before, "provider", "Layout judgment did not return an exact permutation.",
                            fallback=True)
    return ReadingOrder(tuple(ids), "llm", "Accepted a validated ID permutation for ambiguous geometry.")


def _issues(block: OCRBlock) -> tuple[str, ...]:
    label = f"{block.provider_label or ''} {block.provider_type or ''}".casefold()
    if re.search(r"hand[\s_-]*(?:written|writing)", label) or "handwriting" in block.flags:
        return ("handwriting",)
    if block.kind in NON_LESSON:
        return ()
    issues = [flag for flag in block.flags if flag in CONTENT_FLAGS]
    if block.confidence is not None and block.confidence < MIN_CONFIDENCE:
        issues.append("low_confidence")
    text = block.text.strip()
    if "\ufffd" in text or (text and block.kind in {"heading", "paragraph", "caption", "sidebar"}
                           and not any(char.isalnum() for char in text)):
        issues.append("fragment")
    return tuple(issues)


def _crop_png(
    block: OCRBlock, prepared: PreprocessedPage, out: Path,
) -> tuple[bytes, tuple[int, int, int, int]]:
    transform = prepared.transform
    x0, y0, x1, y1 = block.bbox
    width, height = transform.normalized_size
    padding = min(12, max(2, ceil(0.03 * min(x1 - x0, y1 - y0))))
    left, top = max(0, floor(x0) - padding), max(0, floor(y0) - padding)
    right, bottom = min(width, ceil(x1) + padding), min(height, ceil(y1) + padding)
    if left >= right or top >= bottom:
        raise ValueError("Block is outside the normalized page.")
    box = map_box_to_original((left, top, right, bottom), transform)
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError("Block does not map to original pixels.")
    original_bytes = (out / prepared.original_path).read_bytes()
    if sha256(original_bytes).hexdigest() != prepared.input_sha256:
        raise ValueError("Original raster digest differs from preprocessing.")
    (a, b, c), (d, e, f) = transform.normalized_to_original
    # Sample from original pixels while keeping the normalized orientation.
    affine = (a, b, a * left + b * top + c - box[0],
              d, e, d * left + e * top + f - box[1])
    with Image.open(BytesIO(original_bytes)) as image, image.crop(box) as region:
        with region.transform((right - left, bottom - top), Image.Transform.AFFINE, affine,
                              Image.Resampling.BICUBIC, fillcolor="white") as crop:
            return png_bytes(crop), box


def _compatible(original: OCRBlock, candidate: OCRBlock) -> bool:
    if original.kind != candidate.kind or not candidate.text.strip() or _issues(candidate):
        return False
    if "low_confidence" in _issues(original) and candidate.confidence is None:
        return False
    if original.items:
        markers = tuple(item.marker for item in original.items)
        if markers != tuple(item.marker for item in candidate.items or ()):
            return False
    if original.rows and "malformed_table" not in original.flags:
        before = tuple(tuple((cell.rowspan, cell.colspan, cell.is_header) for cell in row)
                       for row in original.rows)
        after = tuple(tuple((cell.rowspan, cell.colspan, cell.is_header) for cell in row)
                      for row in candidate.rows or ())
        if before != after:
            return False
    before_text = "".join(char for char in original.text.casefold() if char.isalnum())
    after_text = "".join(char for char in candidate.text.casefold() if char.isalnum())
    if not before_text:
        return True
    # Require textual agreement alongside crop geometry to avoid accepting neighboring content.
    return SequenceMatcher(None, before_text, after_text, autojunk=False).ratio() >= 0.65


def _corroborates_text(block: OCRBlock, candidates: tuple[CropCandidate, ...]) -> bool:
    """Use crop agreement when the detector score is the sole unresolved issue."""
    if not candidates or any(set(candidate.issues) - {"low_confidence"} for candidate in candidates):
        return False
    if block.kind in TEXT_KINDS:
        # Cropping often removes the context that makes a text block a heading or caption.
        # A detector may also split it into lines. Retain the original type and boundaries.
        if any(candidate.kind not in TEXT_KINDS for candidate in candidates):
            return False
    elif len(candidates) != 1 or candidates[0].kind != block.kind:
        return False
    # Keep case, numbers and punctuation exact. Only ignore whitespace used for wrapping.
    return block.text.split() == " ".join(candidate.text for candidate in candidates).split()


def _repair(
    page: Page, block: OCRBlock, issues: tuple[str, ...], prepared: PreprocessedPage, out: Path,
    ocr: ReadOCR, attempts: AttemptLog,
) -> tuple[OCRBlock, RepairDecision]:
    decision = RepairDecision(block.id, issues, block.text, "skipped", "")
    quarantined = replace(block, flags=tuple(dict.fromkeys((*block.flags, *issues, "quarantined"))))
    if "handwriting" in issues:
        return quarantined, replace(decision, reason="Handwriting is excluded from lesson content.")
    if block.bbox is None or prepared.transform is None or prepared.original_path is None:
        return quarantined, replace(decision, reason="Crop geometry or original raster is unavailable.")
    try:
        png, box = _crop_png(block, prepared, out)
    except (OSError, ValueError, Image.DecompressionBombError):
        return quarantined, replace(decision, reason="Could not prepare a crop from the original raster.")
    result = ocr_page(page, png, lambda _, image: ocr(block.id, image), attempts)
    decision = replace(decision, original_crop_box=box, attempt_ids=result.attempt_ids)
    if result.status == "failed":
        return quarantined, replace(decision, disposition="failed", reason=result.reason or "Crop OCR failed.")
    by_id = {candidate.id: candidate for candidate in result.blocks}
    candidates = tuple(
        by_id[block_id] for block_id in result.ordered_block_ids if by_id[block_id].text.strip()
    )
    crop_evidence = tuple(
        CropCandidate(candidate.kind, candidate.text, candidate.confidence, _issues(candidate))
        for candidate in candidates
    )
    decision = replace(decision, crop_candidates=crop_evidence)
    if issues == ("low_confidence",) and _corroborates_text(block, crop_evidence):
        return replace(block, flags=(*block.flags, "ocr_corroborated")), replace(
            decision, disposition="accepted",
            reason="Crop corroborated the original transcription despite low layout confidence.",
        )
    if len(candidates) != 1 or not _compatible(block, candidates[0]):
        return quarantined, replace(decision, disposition="rejected",
                                    reason="Crop did not yield one compatible block with resolved issues.")

    candidate = candidates[0]
    repaired = replace(
        block, text=candidate.text, items=candidate.items, rows=candidate.rows, latex=candidate.latex,
        flags=tuple(flag for flag in block.flags if flag not in CONTENT_FLAGS) + ("ocr_repaired",),
    )
    return repaired, replace(decision, disposition="accepted", replacement_text=repaired.text,
                             replacement_confidence=candidate.confidence,
                             reason="Crop resolved the OCR issues with compatible structure and text.")


def repair_layout(
    pages: tuple[Page, ...], prepared: tuple[PreprocessedPage, ...], extracted: tuple[OCRPage, ...],
    out: Path, propose: OrderProposal, ocr: ReadOCR, attempts: AttemptLog,
) -> tuple[OCRPage, ...]:
    canonical = []
    decisions = []
    for page, image, record in zip(pages, prepared, extracted, strict=True):
        start = len(attempts.attempts)
        before = record.ordered_block_ids
        order = order_blocks(record, propose)
        order_attempt_ids = tuple(attempt.id for attempt in attempts.attempts[start:])
        blocks = []
        repairs = []
        for block in record.blocks:
            issues = _issues(block)
            if issues:
                block, repair = _repair(page, block, issues, image, out, ocr, attempts)
                repairs.append(repair)
            blocks.append(block)
        quarantined = tuple(block.id for block in blocks if "quarantined" in block.flags)
        if record.status != "failed":
            layout_attempts = tuple(attempts.attempts[start:])
            record = replace(
                record, blocks=tuple(blocks), ordered_block_ids=order.ids,
                status="partial" if quarantined else "ok",
                reason="Some OCR blocks are quarantined." if quarantined else None,
                flags=record.flags + (("layout_fallback",) if order.fallback else ()),
                attempts=record.attempts + layout_attempts,
                attempt_ids=record.attempt_ids + tuple(attempt.id for attempt in layout_attempts),
            )
            write_ocr_page(out, record)
        canonical.append(record)
        decisions.append(PageLayout(
            page.page_id, record.status, before, order.ids,
            order.method, order.reason, order.fallback, quarantined, order_attempt_ids, tuple(repairs),
        ))
    (out / "work/layout.json").write_text(
        json.dumps({"pages": [asdict(decision) for decision in decisions]},
                   ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
    )
    return tuple(canonical)
