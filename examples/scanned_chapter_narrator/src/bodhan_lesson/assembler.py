"""Assembler module: page-aware noise filtering, cross-page stitching, and DocumentIR creation."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from bodhan_lesson.schemas import BlockType, DocumentIR, LessonSection, OCRBlock, PageResult


class LessonAssembler:
    """Assembles individual page OCR results into a coherent, clean DocumentIR."""

    def __init__(self, source_lang: str = "en"):
        self.source_lang = source_lang

    def assemble(self, pages: list[PageResult], output_source_md: Path | None = None) -> DocumentIR:
        """Filter noise, stitch split blocks across pages, and construct DocumentIR."""
        if not pages:
            return DocumentIR(source_language=self.source_lang, total_pages=0)

        # 1. Identify recurring headers and footers across pages
        recurring_noise = self._detect_recurring_noise(pages)

        # 2. Filter noise blocks from each page
        cleaned_pages: list[PageResult] = []
        for p in pages:
            filtered_blocks: list[OCRBlock] = []
            for b in p.blocks:
                if self._is_noise_block(b, recurring_noise):
                    continue
                filtered_blocks.append(b)
            cleaned_pages.append(
                PageResult(
                    page_number=p.page_number,
                    image_path=p.image_path,
                    raw_markdown=p.raw_markdown,
                    blocks=filtered_blocks,
                    detected_headers=list(recurring_noise.get("headers", set())),
                    detected_footers=list(recurring_noise.get("footers", set())),
                    is_doubtful=p.is_doubtful,
                    doubt_reason=p.doubt_reason,
                )
            )

        # 3. Stitch blocks across page boundaries (hyphenated words & broken sentences)
        stitched_blocks = self._stitch_cross_page_blocks(cleaned_pages)

        # 4. Group stitched blocks into logical sections
        sections = self._group_into_sections(stitched_blocks)

        # 5. Assemble master source markdown
        full_markdown_parts: list[str] = []
        for sec in sections:
            full_markdown_parts.append(sec.source_markdown)
        full_source_md = "\n\n".join(full_markdown_parts).strip()

        # Write out/source/lesson.src.md if requested
        if output_source_md:
            output_source_md.parent.mkdir(parents=True, exist_ok=True)
            with open(output_source_md, "w", encoding="utf-8") as f:
                f.write(full_source_md + "\n")

        total_flagged = sum(1 for b in stitched_blocks if b.is_doubtful)

        return DocumentIR(
            source_language=self.source_lang,
            total_pages=len(pages),
            pages=cleaned_pages,
            sections=sections,
            source_markdown=full_source_md,
            flagged_blocks_count=total_flagged,
        )

    def assemble_document(
        self,
        pages: list[PageResult],
        source_language: str = "en",
        output_source_md: Path | None = None,
    ) -> DocumentIR:
        """Filter noise, stitch split blocks across pages, and construct DocumentIR."""
        self.source_lang = source_language
        return self.assemble(pages=pages, output_source_md=output_source_md)

    def _detect_recurring_noise(self, pages: list[PageResult]) -> dict[str, set[str]]:
        """Identify headers, footers, and website watermarks repeated across multiple pages."""
        header_candidates: list[str] = []
        footer_candidates: list[str] = []

        total_pages = len(pages)
        for p in pages:
            for b in p.blocks:
                txt = b.text.strip().lower()
                if not txt:
                    continue
                # If explicit label or header/footer enum
                if b.normalized_type == BlockType.PAGE_HEADER:
                    header_candidates.append(txt)
                elif b.normalized_type == BlockType.PAGE_FOOTER:
                    footer_candidates.append(txt)
                # Position check if bbox available: only for non-content blocks (never headings, equations, or tables)
                elif b.normalized_type not in (BlockType.SECTION_HEADER, BlockType.EQUATION, BlockType.TABLE) and not txt.startswith("#"):
                    if b.bbox_xyxy and len(b.bbox_xyxy) == 4:
                        y0, y1 = b.bbox_xyxy[1], b.bbox_xyxy[3]
                        # Normalized coords [0.0, 1.0]
                        if y1 <= 1.0:
                            if y1 < 0.08:
                                header_candidates.append(txt)
                            elif y0 > 0.92:
                                footer_candidates.append(txt)
                        else:  # pixel coords
                            if y1 < 75:
                                header_candidates.append(txt)
                            elif y0 > 980:
                                footer_candidates.append(txt)

                # Detect common website footers (e.g. Aspirations Institute in NCERT demo)
                if "www." in txt or ".com" in txt or ".org" in txt:
                    footer_candidates.append(txt)

        # Retain strings that appear in 2 or more pages, or >= 30% of pages
        threshold = max(2, int(total_pages * 0.30))
        headers = {k for k, count in Counter(header_candidates).items() if count >= threshold}
        footers = {k for k, count in Counter(footer_candidates).items() if count >= threshold or "www." in k}

        return {"headers": headers, "footers": footers}

    def _is_noise_block(self, block: OCRBlock, noise: dict[str, set[str]]) -> bool:
        """Check if block is an unwanted running header, footer, or page number."""
        txt = block.text.strip().lower()
        if not txt:
            return True

        # Headings, equations, and tables are never noise
        if block.normalized_type in (BlockType.SECTION_HEADER, BlockType.EQUATION, BlockType.TABLE) or block.text.strip().startswith("#"):
            return False

        # Check pure page numbers (e.g. "92", "- 92 -", "Page 5")
        if re.match(r"^(-\s*)?\d+(\s*-)?$", txt) or re.match(r"^page\s+\d+$", txt):
            return True

        # Check against recurring headers and footers
        for h in noise["headers"]:
            if h in txt or txt in h:
                return True
        for f in noise["footers"]:
            if f in txt or txt in f:
                return True

        return False

    def _stitch_cross_page_blocks(self, pages: list[PageResult]) -> list[OCRBlock]:
        """Stitch sentences and hyphenated words split across page boundaries."""
        all_blocks: list[OCRBlock] = []
        for p in pages:
            all_blocks.extend(p.blocks)

        if len(all_blocks) < 2:
            return all_blocks

        stitched: list[OCRBlock] = []
        skip_next = False

        for i in range(len(all_blocks)):
            if skip_next:
                skip_next = False
                continue

            current = all_blocks[i]
            if i + 1 < len(all_blocks):
                nxt = all_blocks[i + 1]

                # Check if current is last on its page and nxt is first on next page
                if (
                    current.page_number != nxt.page_number
                    and current.normalized_type == BlockType.PARAGRAPH
                    and nxt.normalized_type == BlockType.PARAGRAPH
                ):
                    curr_text = current.text.rstrip()
                    nxt_text = nxt.text.lstrip()

                    # Case A: Word broken with trailing hyphen
                    if curr_text.endswith("-"):
                        stem = curr_text[:-1]
                        joined_text = f"{stem}{nxt_text}"
                        merged_block = OCRBlock(
                            block_id=f"{current.block_id}_joined",
                            order=current.order,
                            page_number=current.page_number,
                            raw_type=current.raw_type,
                            raw_label=current.raw_label,
                            normalized_type=BlockType.PARAGRAPH,
                            text=joined_text,
                        )
                        stitched.append(merged_block)
                        skip_next = True
                        continue

                    # Case B: Sentence broken mid-stream (no terminal punctuation)
                    elif curr_text and curr_text[-1] not in ".?!:;।" and nxt_text and nxt_text[0].islower():
                        joined_text = f"{curr_text} {nxt_text}"
                        merged_block = OCRBlock(
                            block_id=f"{current.block_id}_joined",
                            order=current.order,
                            page_number=current.page_number,
                            raw_type=current.raw_type,
                            raw_label=current.raw_label,
                            normalized_type=BlockType.PARAGRAPH,
                            text=joined_text,
                        )
                        stitched.append(merged_block)
                        skip_next = True
                        continue

            stitched.append(current)

        return stitched

    def _group_into_sections(self, blocks: list[OCRBlock]) -> list[LessonSection]:
        """Group blocks into logical pedagogical sections based on headings."""
        sections: list[LessonSection] = []
        current_title = "Overview & Introduction"
        current_level = 1
        current_blocks: list[OCRBlock] = []
        sec_idx = 1

        for b in blocks:
            # Detect section heading across diverse formats (Markdown #, numbered 1.1 or 5.6.1, or named Chapter/Section)
            is_heading = (
                b.normalized_type == BlockType.SECTION_HEADER
                or bool(
                    re.match(
                        r"^(?:#{1,6}\s+|(?:\d+\.)+\d*\s+[A-Z]|(?:Chapter|Section|Theorem|Axiom|Example)\s+\d+)",
                        b.text.strip(),
                    )
                )
            )

            if is_heading and current_blocks:
                # Save completed section
                sec = self._build_section(sec_idx, current_title, current_level, current_blocks)
                sections.append(sec)
                sec_idx += 1

                # Start new section
                first_line = b.text.strip().split("\n")[0].lstrip("#").strip()[:80]
                current_title = first_line or f"Section {sec_idx}"
                current_level = b.text.count("#") if b.text.startswith("#") else 2
                current_blocks = [b]
            else:
                if is_heading:
                    first_line = b.text.strip().split("\n")[0].lstrip("#").strip()[:80]
                    current_title = first_line or f"Section {sec_idx}"
                    current_level = b.text.count("#") if b.text.startswith("#") else 2
                current_blocks.append(b)

        if current_blocks:
            sec = self._build_section(sec_idx, current_title, current_level, current_blocks)
            sections.append(sec)

        return sections

    def _build_section(self, index: int, title: str, level: int, blocks: list[OCRBlock]) -> LessonSection:
        """Construct a LessonSection model with equations, tables, and figure captions extracted."""
        raw_slug = re.sub(r"[^a-zA-Z0-9]+", "_", title.lower()).strip("_")
        clean_slug = raw_slug[:35].rstrip("_") or f"section_{index:02d}"
        sec_id = f"sec_{index:02d}_{clean_slug}"

        text_chunks: list[str] = []
        equations: list[str] = []
        tables: list[str] = []
        captions: list[str] = []

        for b in blocks:
            text_chunks.append(b.text)
            if b.normalized_type == BlockType.EQUATION or "$" in b.text:
                equations.append(b.text)
            elif b.normalized_type == BlockType.TABLE or ("|" in b.text and "-|-" in b.text):
                tables.append(b.text)
            elif b.normalized_type == BlockType.FIGURE_CAPTION or "figure" in b.text.lower():
                captions.append(b.text)

        markdown_body = "\n\n".join(text_chunks).strip()
        words = len(markdown_body.split())

        return LessonSection(
            section_id=sec_id,
            section_slug=clean_slug,
            title=title,
            level=level,
            blocks=blocks,
            source_markdown=markdown_body,
            equations=equations,
            tables=tables,
            figure_captions=captions,
            word_count=words,
        )


assembler = LessonAssembler()

