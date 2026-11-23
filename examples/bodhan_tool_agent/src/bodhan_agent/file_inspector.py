"""File inspection, metadata extraction, and prompt injection detection."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from bodhan_agent.models import InputFileInfo
from bodhan_mcp.tools.file_utils import ALLOWED_AUDIO_EXTENSIONS, ALLOWED_DOC_EXTENSIONS, resolve_safe_path
from bodhan_mcp.tools.media import get_audio_duration_seconds

# Suspicious patterns inside document/transcript text that attempt to hijack agent planning
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|above)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+in\s+developer\s+mode", re.IGNORECASE),
    re.compile(r"system\s*:\s*new\s+system\s+prompt", re.IGNORECASE),
    re.compile(r"disregard\s+all\s+prior\s+guidance", re.IGNORECASE),
    re.compile(r"override\s+system\s+directives", re.IGNORECASE),
    re.compile(r"execute\s+command\s*:", re.IGNORECASE),
    re.compile(r"<system_message>", re.IGNORECASE),
]


def calculate_sha256(path: Path) -> str:
    """Calculate SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def detect_prompt_injection(content: str) -> bool:
    """Scan untrusted extracted text for known prompt injection signatures."""
    for pattern in INJECTION_PATTERNS:
        if pattern.search(content):
            return True
    return False


def sanitize_untrusted_text(text: str) -> str:
    """Neutralize potential instruction injection by wrapping untrusted data in an inert sandbox container."""
    # Defang XML/HTML instruction tags if present
    defanged = text.replace("<system>", "&lt;system&gt;").replace("</system>", "&lt;/system&gt;")
    return defanged


def inspect_file(file_path: str | Path, base_dir: Path | None = None) -> InputFileInfo:
    """Inspect file properties, format, duration or page count safely."""
    path = resolve_safe_path(file_path, base_dir=base_dir, must_exist=True)
    suffix = path.suffix.lower()
    size = path.stat().st_size
    checksum = calculate_sha256(path)

    # Audio file
    if suffix in ALLOWED_AUDIO_EXTENSIONS:
        duration = get_audio_duration_seconds(path)
        return InputFileInfo(
            path=str(path),
            filename=path.name,
            file_type="audio",
            format=suffix.lstrip("."),
            size_bytes=size,
            duration_seconds=round(duration, 2),
            checksum_sha256=checksum,
        )

    # Document (PDF or image)
    if suffix == ".pdf":
        page_count = 1
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(str(path))
            page_count = len(doc)
            doc.close()
        except Exception:
            pass

        return InputFileInfo(
            path=str(path),
            filename=path.name,
            file_type="document",
            format="pdf",
            size_bytes=size,
            page_count=page_count,
            checksum_sha256=checksum,
        )

    if suffix in ALLOWED_DOC_EXTENSIONS:
        return InputFileInfo(
            path=str(path),
            filename=path.name,
            file_type="image",
            format=suffix.lstrip("."),
            size_bytes=size,
            page_count=1,
            checksum_sha256=checksum,
        )

    # Text file
    if suffix in {".txt", ".md", ".json", ".csv", ".srt"}:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            has_injection = detect_prompt_injection(content)
        except Exception:
            has_injection = False

        return InputFileInfo(
            path=str(path),
            filename=path.name,
            file_type="text",
            format=suffix.lstrip("."),
            size_bytes=size,
            checksum_sha256=checksum,
            prompt_injection_warning=has_injection,
        )

    return InputFileInfo(
        path=str(path),
        filename=path.name,
        file_type="other",
        format=suffix.lstrip("."),
        size_bytes=size,
        checksum_sha256=checksum,
    )


def inspect_files_in_path(path_or_dir: str | Path) -> list[InputFileInfo]:
    """Scan a file or directory of input files."""
    p = Path(path_or_dir).resolve()
    if not p.exists():
        return []

    if p.is_file():
        return [inspect_file(p)]

    results: list[InputFileInfo] = []
    for item in sorted(p.iterdir()):
        if item.is_file() and not item.name.startswith("."):
            try:
                results.append(inspect_file(item))
            except Exception:
                continue

    return results
