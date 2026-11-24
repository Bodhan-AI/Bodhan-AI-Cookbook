"""OCR normalizer module: calls Indic-OCR and normalizes response into PageResult."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from bodhan_lesson.client import bodhan_client
from bodhan_lesson.schemas import BlockType, OCRBlock, PageResult

logger = logging.getLogger("bodhan_lesson")


class OCREngine:
    """Invokes Indic-OCR and normalizes API output into structured PageResult models."""

    def __init__(self, client=None):
        self.client = client or bodhan_client

    def process_page(
        self,
        image_path: str | Path,
        page_number: int,
        raw_log_dir: Path | None = None,
    ) -> PageResult:
        """Process one page image through Indic-OCR and return a normalized PageResult."""
        img_p = Path(image_path).resolve()
        with open(img_p, "rb") as f:
            image_bytes = f.read()

        ext = img_p.suffix.lstrip(".").lower()
        if ext == "jpg":
            ext = "jpeg"

        # Call Bodhan Indic-OCR
        ocr_response = self.client.ocr_page(image_bytes=image_bytes, image_format=ext)

        # Audit logging: write raw API response if requested
        if raw_log_dir:
            raw_log_dir.mkdir(parents=True, exist_ok=True)
            log_file = raw_log_dir / f"page_{page_number:02d}_ocr.json"
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(ocr_response, f, indent=2, ensure_ascii=False)

        markdown_text = ocr_response.get("markdown", "")
        raw_blocks = ocr_response.get("blocks", [])

        normalized_blocks: list[OCRBlock] = []

        if raw_blocks:
            for idx, rb in enumerate(raw_blocks):
                block = self._normalize_block(rb, page_number=page_number, default_order=idx)
                normalized_blocks.append(block)
        else:
            # Fallback if API returned plain markdown without layout blocks array
            normalized_blocks = self._parse_markdown_into_blocks(markdown_text, page_number)

        # Overall page doubt check
        doubtful_blocks = [b for b in normalized_blocks if b.is_doubtful]
        is_page_doubtful = len(doubtful_blocks) > len(normalized_blocks) // 2

        return PageResult(
            page_number=page_number,
            image_path=img_p,
            raw_markdown=markdown_text,
            blocks=normalized_blocks,
            is_doubtful=is_page_doubtful,
            doubt_reason=f"{len(doubtful_blocks)} doubtful blocks detected" if is_page_doubtful else None,
        )

    def _normalize_block(self, raw_block: dict[str, Any], page_number: int, default_order: int) -> OCRBlock:
        """Map raw API block dictionary into strongly typed OCRBlock."""
        raw_type = str(raw_block.get("type", "")).strip()
        raw_label = str(raw_block.get("label", "")).strip()
        text = str(raw_block.get("text", "")).strip()
        order = int(raw_block.get("order", default_order))
        bbox = raw_block.get("bbox_xyxy")
        conf = float(raw_block["conf"]) if raw_block.get("conf") is not None else None

        # Determine normalized enum type
        norm_type = self._classify_block_type(raw_type, raw_label, text)

        # Detect doubtful or corrupted content
        is_doubtful, doubt_reason = self._check_doubtful(text, conf)

        # Extract LaTeX if present
        raw_latex = None
        if norm_type == BlockType.EQUATION or "$" in text:
            raw_latex = self._extract_latex(text)

        block_id = f"p{page_number:02d}_b{order:02d}"

        return OCRBlock(
            block_id=block_id,
            order=order,
            page_number=page_number,
            raw_type=raw_type,
            raw_label=raw_label,
            normalized_type=norm_type,
            text=text,
            raw_latex=raw_latex,
            bbox_xyxy=bbox,
            confidence=conf,
            is_doubtful=is_doubtful,
            doubt_reason=doubt_reason,
        )

    def _classify_block_type(self, raw_type: str, raw_label: str, text: str) -> BlockType:
        """Heuristic and label classifier for block types."""
        combined = f"{raw_type} {raw_label}".lower()

        if "header" in combined or "runninghead" in combined:
            return BlockType.PAGE_HEADER
        elif "footer" in combined or "pagefooter" in combined:
            return BlockType.PAGE_FOOTER
        elif "table" in combined or "|" in text and "-|-" in text:
            return BlockType.TABLE
        elif "equation" in combined or "formula" in combined or text.startswith("$$") or text.startswith("$"):
            return BlockType.EQUATION
        elif "caption" in combined:
            return BlockType.FIGURE_CAPTION
        elif "picture" in combined or "figure" in combined:
            return BlockType.FIGURE
        elif text.startswith("#"):
            return BlockType.SECTION_HEADER
        elif text.startswith("- ") or text.startswith("* ") or re.match(r"^\d+\.\s", text):
            return BlockType.LIST_ITEM
        elif raw_type or raw_label:
            return BlockType.PARAGRAPH
        else:
            return BlockType.UNKNOWN

    def _check_doubtful(self, text: str, conf: float | None) -> tuple[bool, str | None]:
        """Detect low confidence, unclosed LaTeX delimiters, or corrupted OCR strings."""
        if conf is not None and conf < 0.40:
            return True, f"Low OCR confidence ({conf:.2f})"

        # Check for unclosed single dollar or double dollar LaTeX delimiters
        single_dollars = re.findall(r"(?<!\\)\$", text)
        if len(single_dollars) % 2 != 0:
            return True, "Unclosed LaTeX math delimiter"

        # Check for high garbled symbol ratio
        if len(text) > 10:
            unprintable = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t")
            if unprintable / len(text) > 0.15:
                return True, "Excessive unprintable characters"

        return False, None

    def _extract_latex(self, text: str) -> str:
        """Extract all display or inline LaTeX math segments."""
        matches = re.findall(r"\$\$[\s\S]*?\$\$|\$[^\$\n]+\$", text)
        return " ".join(matches) if matches else text

    def _parse_markdown_into_blocks(self, markdown: str, page_number: int) -> list[OCRBlock]:
        """Fallback block parser if API only returns flat markdown."""
        lines = markdown.split("\n\n")
        blocks: list[OCRBlock] = []
        for idx, chunk in enumerate(lines):
            chunk = chunk.strip()
            if not chunk:
                continue
            norm_type = self._classify_block_type("", "", chunk)
            is_doubtful, doubt_reason = self._check_doubtful(chunk, None)
            blocks.append(
                OCRBlock(
                    block_id=f"p{page_number:02d}_b{idx:02d}",
                    order=idx,
                    page_number=page_number,
                    raw_type="MarkdownChunk",
                    raw_label="AutoChunk",
                    normalized_type=norm_type,
                    text=chunk,
                    is_doubtful=is_doubtful,
                    doubt_reason=doubt_reason,
                )
            )
        return blocks

    def process_document_pages(
        self,
        page_paths: list[Path],
        raw_log_dir: Path | None = None,
    ) -> list[PageResult]:
        """Process all sequential page images in a document."""
        results: list[PageResult] = []
        for idx, p in enumerate(page_paths, start=1):
            try:
                res = self.process_page(p, page_number=idx, raw_log_dir=raw_log_dir)
            except Exception as e:
                logger.error(
                    f"OCR failed for page {idx} ({p.name}) after all retries: {e}. "
                    "Flagging page as doubtful to ensure full document pipeline continuity."
                )
                res = PageResult(
                    page_number=idx,
                    image_path=p,
                    raw_markdown=f"<!-- Page {idx} OCR unavailable: {e} -->",
                    blocks=[],
                    is_doubtful=True,
                    doubt_reason=f"OCR error: {e}",
                )
            results.append(res)
        return results


ocr_processor = OCREngine()

