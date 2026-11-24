from __future__ import annotations

from ..languages import detect_script_language, normalize_lang_code
from ..models import ErrorCode, ToolError, failure, success
from ..workspace import Workspace

_TEXT_SUFFIXES = {".md", ".markdown", ".srt", ".txt", ".vtt"}


def detect_language(text_or_file: str, workspace: Workspace | None = None) -> dict:
    try:
        if not isinstance(text_or_file, str) or not text_or_file.strip():
            raise ToolError(ErrorCode.INVALID_ARGUMENT, "`text_or_file` must be non-empty text.")

        value = text_or_file.strip()
        if Workspace.is_probable_file_ref(value, _TEXT_SUFFIXES):
            if workspace is None:
                raise ToolError(
                    ErrorCode.INVALID_ARGUMENT,
                    "A workspace is required to detect language from a file.",
                    hint="Pass a Workspace or provide the text directly.",
                )
            path = workspace.resolve_input(value, suffixes=_TEXT_SUFFIXES)
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ToolError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Could not decode {path.name!r} as UTF-8 text.",
                    hint="Save the file as UTF-8 and try again.",
                ) from exc
        else:
            text = value

        detected = detect_script_language(text)
        candidates = detected["candidates"]
        if detected["confidence"] <= 0:
            result = {
                "script": "UNKNOWN",
                "candidates": [],
                "ambiguous": False,
                "confidence": 0.0,
            }
        else:
            result = {
                **detected,
                "candidates": [normalize_lang_code(code) for code in candidates],
            }
        return success(result)
    except ToolError as err:
        return failure(err)
    except OSError as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"Could not read language input: {exc.strerror or exc}.",
            )
        )