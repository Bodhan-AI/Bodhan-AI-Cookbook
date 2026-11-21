from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from bodhan_mcp.schemas.base import UNTRUSTED_CONTENT_DESCRIPTION, ToolInput, ToolOutput

TableFormat = Literal["markdown", "html"]
OcrLanguageSource = Literal["detected", "undetermined"]

_PAGES_RE = re.compile(r"^\s*\d+\s*(?:-\s*\d+\s*)?(?:,\s*\d+\s*(?:-\s*\d+\s*)?)*$")


def parse_pages(spec: str) -> list[int]:
    if not _PAGES_RE.match(spec):
        raise ValueError("pages must look like '1', '1-3' or '1,3,5-8'.")
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        first, _, last = part.partition("-")
        start, end = int(first), int(last or first)
        if start < 1 or end < start:
            raise ValueError(f"'{part}' is not a valid page range; pages start at 1 and ranges go upwards.")
        pages.extend(range(start, end + 1))
    if len(pages) != len(set(pages)):
        raise ValueError("pages lists the same page more than once.")
    return pages


@dataclass(frozen=True)
class OcrResult:
    markdown: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    request_id: str | None = None


class OcrInput(ToolInput):
    file_path: str = Field(
        description=(
            "Path to a scanned document or photo: PDF, PNG or JPEG (WebP, BMP and TIFF are converted). Relative "
            "paths are resolved against the server's input folder. The file type is checked from its contents."
        ),
    )
    pages: str | None = Field(
        default=None,
        description=(
            "PDF only: pages to read, like '1', '1-3' or '1,3,5-8'. Defaults to every page, up to the server's "
            "page limit."
        ),
    )
    table_format: TableFormat = Field(
        default="markdown",
        description=(
            "'markdown' gives pipe tables that read well and survive translation. 'html' keeps merged cells and "
            "multi-level headers."
        ),
    )
    output_path: str | None = Field(
        default=None,
        description=(
            "Where to write the extracted Markdown (.md), inside the server's output folder. Layout blocks are "
            "written next to it as JSON. Defaults to a name derived from the input."
        ),
    )
    overwrite: bool = Field(default=False, description="Replace output_path and its blocks file if they exist.")

    @model_validator(mode="after")
    def _check(self) -> OcrInput:
        if not self.file_path.strip():
            raise ValueError("file_path must not be empty.")
        if self.pages is not None:
            parse_pages(self.pages)
        if self.output_path is not None:
            if Path(self.output_path.strip()).suffix.lower() != ".md":
                raise ValueError("output_path must end in .md.")
        return self


class OcrOutput(ToolOutput):
    output_path: str = Field(description="Markdown file with the full extracted text, pages in order.")
    blocks_path: str = Field(
        description="JSON file with every page's layout blocks (label, bounding box, confidence, text)."
    )
    text: str = Field(description="The extracted Markdown, or its first part when text_is_preview is true.")
    text_is_preview: bool = Field(description="True when text is truncated; read output_path for all.")
    pages_read: list[int] = Field(description="Page numbers read successfully, in order; [1] for a single image.")
    unread_pages: list[int] = Field(
        default_factory=list,
        description=(
            "Pages that could not be read; the other pages are complete. The Markdown marks each one with "
            "'[Page N could not be read.]' and warnings give the reason. Retry only these pages with `pages` when "
            "the reason says it is worth retrying."
        ),
    )
    script: str | None = Field(description="Main writing system of the text, such as 'Devanagari' or 'Tamil'.")
    language: str | None = Field(
        description="Language code, only when the script is used by a single supported language; otherwise null."
    )
    language_source: OcrLanguageSource = Field(
        description=(
            "'detected': the script points to one language (for example Tamil or Telugu script). "
            "'undetermined': the script is shared by several languages (Devanagari, Bengali, Perso-Arabic, Latin) "
            "or no text was found; see candidate_languages."
        ),
    )
    candidate_languages: list[str] = Field(
        default_factory=list,
        description="Languages written in the detected script when undetermined. Not a ranked detection result.",
    )
    untrusted_content: Literal[True] = Field(default=True, description=UNTRUSTED_CONTENT_DESCRIPTION)
    warnings: list[str] = Field(default_factory=list)
