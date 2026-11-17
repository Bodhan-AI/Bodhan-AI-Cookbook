from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.utils.process import run_process, stderr_tail

MAX_SIDE_PX = 2000
DOCUMENT_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})
_PAGE_COUNT_RE = re.compile(r"^Pages:\s+(\d+)\s*$", re.MULTILINE)
_ENCRYPTED_RE = re.compile(r"^Encrypted:\s+yes", re.MULTILINE)
_FORMAT_HINT = "Use a PDF, PNG or JPEG file (WebP, BMP and TIFF images are converted)."


def sniff_document(head: bytes) -> str | None:
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"BM"):
        return "bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


def detect_document(path: Path) -> str:
    if path.suffix.lower() not in DOCUMENT_EXTENSIONS:
        raise BodhanToolError(
            "unsupported_file",
            f"{path.name} is not a supported document or image.",
            hint=_FORMAT_HINT,
            details={"extension": path.suffix},
        )
    with path.open("rb") as handle:
        kind = sniff_document(handle.read(16))
    if kind is None:
        raise BodhanToolError(
            "unsupported_file",
            f"{path.name} is not a readable PDF or image, whatever its extension says.",
            hint=_FORMAT_HINT,
            details={"path": path.name},
        )
    return kind


def check_pages(pages: list[int], page_count: int, max_pages: int) -> None:
    beyond = [page for page in pages if page > page_count]
    if beyond:
        raise BodhanToolError(
            "invalid_input",
            f"The document has {page_count} page(s); page {beyond[0]} does not exist.",
            details={"page_count": page_count},
        )
    if len(pages) > max_pages:
        raise BodhanToolError(
            "input_too_large",
            f"{len(pages)} pages were requested; at most {max_pages} are read per call.",
            hint="Pass a smaller `pages` range, or raise BODHAN_OCR_MAX_PAGES.",
            details={"page_count": page_count, "requested": len(pages)},
        )


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise BodhanToolError(
            "internal_error",
            f"{name} is required for document reading but was not found on PATH.",
            hint="Install ffmpeg and poppler-utils (pdfinfo, pdftoppm) and restart the server.",
        )
    return path


async def probe_pdf_pages(path: Path, *, deadline: float) -> int:
    code, stdout, stderr = await run_process([_tool("pdfinfo"), str(path)], deadline, "Reading the PDF")
    info = stdout.decode("utf-8", errors="replace")
    if code != 0:
        raise BodhanToolError(
            "unsupported_file",
            f"{path.name} could not be read as a PDF.",
            hint=_FORMAT_HINT,
            details={"path": path.name, "pdfinfo": stderr_tail(stderr, path)},
        )
    if _ENCRYPTED_RE.search(info):
        raise BodhanToolError(
            "unsupported_file",
            f"{path.name} is encrypted.",
            hint="Remove the password protection and try again.",
            details={"path": path.name},
        )
    match = _PAGE_COUNT_RE.search(info)
    if not match or int(match.group(1)) < 1:
        raise BodhanToolError("unsupported_file", f"{path.name} has no pages.", details={"path": path.name})
    return int(match.group(1))


async def render_pdf_pages(
    path: Path,
    pages: list[int],
    workdir: Path,
    *,
    deadline: float,
    max_side: int = MAX_SIDE_PX,
) -> list[Path]:
    pdftoppm = _tool("pdftoppm")
    rendered: list[Path] = []
    for page in pages:
        prefix = workdir / f"page_{page:04d}"
        dest = prefix.with_suffix(".png")
        if dest.exists():
            raise BodhanToolError("file_exists", f"{dest.name} already exists.", details={"path": dest.name})
        code, _, stderr = await run_process(
            [pdftoppm, "-q", "-f", str(page), "-l", str(page), "-png", "-scale-to", str(max_side),
             "-singlefile", str(path), str(prefix)],
            deadline,
            f"Rendering PDF page {page}",
        )  # fmt: skip
        if code != 0 or not dest.is_file() or dest.stat().st_size == 0:
            raise BodhanToolError(
                "unsupported_file",
                f"Page {page} of {path.name} could not be rendered.",
                hint=_FORMAT_HINT,
                details={"page": page, "pdftoppm": stderr_tail(stderr, path)},
            )
        rendered.append(dest)
    return rendered


async def _image_size(path: Path, *, deadline: float) -> tuple[int, int]:
    code, stdout, stderr = await run_process(
        [_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "json", str(path)],
        deadline,
        "Reading the image",
    )  # fmt: skip
    try:
        stream = json.loads(stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (ValueError, KeyError, IndexError, TypeError):
        width = height = 0
    if code != 0 or width <= 0 or height <= 0:
        raise BodhanToolError(
            "unsupported_file",
            f"{path.name} could not be decoded as an image.",
            hint=_FORMAT_HINT,
            details={"path": path.name, "ffprobe": stderr_tail(stderr, path)},
        )
    return width, height


async def prepare_image(
    path: Path,
    kind: str,
    workdir: Path,
    *,
    deadline: float,
    max_side: int = MAX_SIDE_PX,
) -> Path:
    await _image_size(path, deadline=deadline)
    suffix, codec = (".jpg", ["-q:v", "2"]) if kind == "jpeg" else (".png", [])
    dest = workdir / f"{path.stem}.prepared{suffix}"
    if dest.exists():
        raise BodhanToolError("file_exists", f"{dest.name} already exists.", details={"path": dest.name})
    scale = f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease"
    code, _, stderr = await run_process(
        [_tool("ffmpeg"), "-hide_banner", "-v", "error", "-i", str(path), "-frames:v", "1",
         "-vf", scale, "-map_metadata", "-1", *codec, str(dest)],
        deadline,
        "Preparing the image",
    )  # fmt: skip
    if code != 0 or not dest.is_file() or dest.stat().st_size == 0:
        raise BodhanToolError(
            "unsupported_file",
            f"{path.name} could not be converted for reading.",
            hint=_FORMAT_HINT,
            details={"path": path.name, "ffmpeg": stderr_tail(stderr, path)},
        )
    return dest
