"""Plain-text file helpers: the agent's way to read text inputs and write text outputs (SRT, Markdown, ...).

No model is involved. Writes are confined to one root folder (``BODHAN_OUTPUT_ROOT``, default: the
server's working directory) so text found inside untrusted inputs cannot make a caller overwrite
arbitrary files.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from bodhan_core.errors import ToolError, check_input_file

MAX_READ_CHARS = 50_000
MAX_WRITE_CHARS = 1_000_000


class ReadTextResult(BaseModel):
    ok: bool
    text: str | None = None
    total_chars: int = 0
    offset: int = 0
    truncated: bool = False  # true: more text remains; call again with offset = offset + len(text)
    error: ToolError | None = None


class WriteTextResult(BaseModel):
    ok: bool
    output_path: str | None = None
    chars_written: int = 0
    error: ToolError | None = None


def output_root() -> Path:
    return Path(os.environ.get("BODHAN_OUTPUT_ROOT") or Path.cwd()).resolve()


def read_text_file(file_path: str, offset: int = 0, max_chars: int = 20_000) -> ReadTextResult:
    path = Path(file_path)
    if (err := check_input_file(path, "file_path", "text")) is not None:
        return ReadTextResult(ok=False, error=err)
    if offset < 0 or not 1 <= max_chars <= MAX_READ_CHARS:
        return ReadTextResult(ok=False, error=ToolError(
            code="invalid_argument", message=f"`offset` must be >= 0 and `max_chars` between 1 and {MAX_READ_CHARS}."))
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        return ReadTextResult(ok=False, error=ToolError(
            code="not_text", message=f"`{path.name}` is not UTF-8 text. Use `ocr_document` for images/PDFs or `transcribe_audio` for audio."))
    chunk = text[offset : offset + max_chars]
    return ReadTextResult(ok=True, text=chunk, total_chars=len(text), offset=offset, truncated=offset + len(chunk) < len(text))


def write_text_file(output_path: str, content: str) -> WriteTextResult:
    root = output_root()
    path = Path(output_path)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        return WriteTextResult(ok=False, error=ToolError(
            code="path_outside_output_root", message=f"Refusing to write `{path}`: files may only be written under `{root}`. Pass a path inside that folder."))
    if len(content) > MAX_WRITE_CHARS:
        return WriteTextResult(ok=False, error=ToolError(code="content_too_large", message=f"`content` is {len(content)} characters; the limit is {MAX_WRITE_CHARS}."))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    except OSError as exc:
        return WriteTextResult(ok=False, error=ToolError(code="write_failed", message=f"Could not write `{path}`: {exc.strerror or exc}.", retryable=False))
    return WriteTextResult(ok=True, output_path=str(path), chars_written=len(content))
