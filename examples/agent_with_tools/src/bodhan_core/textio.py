"""Moving text between tools by file reference, so long OCR / transcript / translation text never has to pass
through the LLM's context.

* A tool that produces long text saves it to the work folder and returns the path (plus a short preview for English text only; never for Indian-language text).
* A tool that consumes text accepts either inline ``text`` or a ``text_path``.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from bodhan_core.config import section
from bodhan_core.errors import ToolError, check_input_file

INLINE_CHARS = section("output")["inline_chars"]
PREVIEW_CHARS = 600


def work_dir() -> Path:
    """Folder for intermediate files (BODHAN_WORK_DIR; the agent points it at <out>/work)."""
    d = Path(os.environ.get("BODHAN_WORK_DIR") or Path(tempfile.gettempdir()) / "bodhan_work")
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def save_work(text: str, stem: str, suffix: str = ".txt") -> str:
    """Save text under the work folder. The name comes from the content hash, so the same text maps to one file."""
    path = work_dir() / f"{stem}_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:10]}{suffix}"
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path)


def save_to(text: str, output_path: str | None, stem: str, suffix: str = ".txt") -> tuple[str | None, ToolError | None]:
    """Write to the caller's ``output_path`` if given, else (for long text only) to the work folder."""
    if output_path:
        p = Path(output_path)
        if root_env := os.environ.get("BODHAN_OUTPUT_ROOT"):  # set by the agent: untrusted text must not choose where files land
            root = Path(root_env).resolve()
            p = (p if p.is_absolute() else root / p).resolve()
            if not p.is_relative_to(root):
                return None, ToolError(code="path_outside_output_root", message=f"Refusing to write `{p}`: files may only be written under `{root}`. Pass a path inside that folder.")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:
            return None, ToolError(code="output_not_writable", message=f"Could not write `{p}`: {exc.strerror or exc}. Choose a writable `output_path`.")
        return str(p.resolve()), None
    if len(text) > INLINE_CHARS:
        return save_work(text, stem, suffix), None
    return None, None


def _is_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return not letters or sum(c.isascii() for c in letters) / len(letters) >= 0.5


def inline(text: str) -> tuple[str, bool]:
    """(text to put in the response, whether it is only a preview of longer text).

    Long English/Latin text gets a short preview. Long Indian-language text gets NO preview (empty string):
    the planner must not see a fragment it could copy into `text=`, which would translate only the fragment
    and re-type Indic text the LLM is not allowed to write. It works from the path and the metadata instead.
    """
    if len(text) <= INLINE_CHARS:
        return text, False
    return (text[:PREVIEW_CHARS] if _is_latin(text) else ""), True


def resolve_text(text: str | None, text_path: str | None) -> str | ToolError:
    """Exactly one of ``text`` / ``text_path``. Returns the text, or an actionable error."""
    if text and text_path:
        return ToolError(code="invalid_argument", message="Pass either `text` or `text_path`, not both.")
    if text_path:
        path = Path(text_path)
        if err := check_input_file(path, "text_path", "UTF-8 text"):
            return err
        try:
            return path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError:
            return ToolError(code="not_text", message=f"`{path.name}` is not UTF-8 text. Use `ocr_document` for images/PDFs or `transcribe_audio` for audio first.")
    if text is None or not text.strip():
        return ToolError(code="empty_text", message="`text` is empty. Provide the text, or `text_path` pointing at a text file.")
    return text
