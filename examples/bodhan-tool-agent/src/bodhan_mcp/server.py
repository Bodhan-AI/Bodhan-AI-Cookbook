
from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from .cache import CostLedger, DiskCache
from .client import BodhanClient
from .models import ErrorCode, ToolError
from .tools.capabilities import list_capabilities
from .tools.chunking import chunk_audio_manifest
from .tools.detect_language import detect_language
from .tools.inspect import inspect_media
from .tools.ocr import ocr_document
from .tools.speak import speak_text
from .tools.transcribe import transcribe_audio
from .tools.translate import translate_text
from .workspace import Workspace

log = logging.getLogger(__name__)
app = Server("bodhan-mcp-server")

workspace = Workspace.from_env()
client = BodhanClient()
cache = DiskCache.from_env()
ledger = CostLedger.from_env()

_TEXT_RESULT = {
    "anyOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "preview": {"type": "string"},
                "artifact_ref": {"type": ["string", "null"]},
                "character_count": {"type": "integer"},
            },
            "required": ["preview", "artifact_ref", "character_count"],
            "additionalProperties": False,
        },
    ]
}

_ARTIFACT_SCHEMA = {
    "type": "object",
    "properties": {
        "ref": {"type": "string"},
        "path": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": [
                "audio",
                "text",
                "markdown",
                "subtitles",
                "transcript",
                "translation",
                "ocr",
                "json",
                "image",
                "other",
            ],
        },
        "mime": {"type": "string"},
        "bytes": {"type": "integer", "minimum": 0},
    },
    "required": ["ref", "path", "kind", "mime", "bytes"],
    "additionalProperties": False,
}
_USAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "model": {"type": "string"},
        "cost_inr": {"type": "number", "minimum": 0},
        "latency_ms": {"type": "integer", "minimum": 0},
        "cached": {"type": "boolean"},
        "avoided_inr": {"type": "number", "minimum": 0},
        "units": {"type": "object", "additionalProperties": {"type": "number"}},
    },
    "required": ["model", "cost_inr", "latency_ms", "cached", "units"],
    "additionalProperties": False,
}
_ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "enum": [code.value for code in ErrorCode],
        },
        "message": {"type": "string"},
        "retryable": {"type": "boolean"},
        "hint": {"type": ["string", "null"]},
        "details": {"type": "object"},
    },
    "required": ["code", "message", "retryable", "hint"],
    "additionalProperties": False,
}


def _object_schema(
    properties: dict[str, Any],
    required: list[str] | None = None,
    *,
    additional_properties: bool | dict[str, Any] = False,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": additional_properties,
    }


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
    data_properties: dict[str, Any] | None = None,
    data_required: list[str] | None = None,
) -> Tool:
    data_schema = _object_schema(data_properties or {}, data_required)
    success_schema = _object_schema(
        {
            "ok": {"type": "boolean", "enum": [True]},
            "data": data_schema,
            "artifacts": {"type": "array", "items": _ARTIFACT_SCHEMA},
            "usage": {"anyOf": [_USAGE_SCHEMA, {"type": "null"}]},
            "warnings": {"type": "array", "items": {"type": "string"}},
        }
    )
    failure_schema = _object_schema(
        {
            "ok": {"type": "boolean", "enum": [False]},
            "error": _ERROR_SCHEMA,
        }
    )
    return Tool(
        name=name,
        description=description,
        inputSchema=_object_schema(properties, required or [], additional_properties=False),
        outputSchema={"oneOf": [success_schema, failure_schema]},
    )


def _build_tools() -> list[Tool]:
    return [
        _tool(
            "transcribe_audio",
            "Transcribe audio or video audio track from a workspace file path or "
            "artifact:<ref>. Media is automatically segmented into <=30s windows; "
            "pass output_format='srt' for timestamped SRT subtitles.",
            {
                "file_path": {
                    "type": "string",
                    "description": "Workspace file path or artifact:<ref>.",
                },
                "language": {
                    "type": ["string", "null"],
                    "description": "Optional language code, e.g. hi or ta.",
                },
                "output_format": {
                    "type": "string",
                    "enum": ["text", "srt"],
                    "description": "Output format: 'text' (default) or 'srt'.",
                },
            },
            ["file_path"],
            {
                "source_path": {"type": "string"},
                "transcript": _TEXT_RESULT,
                "language_detected": {"type": ["string", "null"]},
                "duration_seconds": {"type": ["number", "null"]},
                "chunk_count": {"type": "integer"},
                "output_format": {"type": "string"},
                "segments": {"type": "array", "items": {"type": "object"}},
                "coverage_complete": {"type": "boolean"},
            },
        ),
        _tool(
            "translate_text",
            "Translate text or a UTF-8 file (.txt, .md, .srt, .vtt). Long inputs are "
            "split safely, and SRT timestamps or Markdown table rows are preserved.",
            {
                "text": {
                    "type": "string",
                    "description": "Text, workspace path, or artifact:<ref>.",
                },
                "target_language": {"type": "string", "description": "Target language code."},
                "source_language": {
                    "type": ["string", "null"],
                    "description": "Optional source language code.",
                },
            },
            ["text", "target_language"],
            {
                "translation": _TEXT_RESULT,
                "target_language": {"type": "string"},
                "source_language": {"type": ["string", "null"]},
                "source_character_count": {"type": "integer"},
                "character_count": {"type": "integer"},
                "chunk_count": {"type": "integer"},
                "translated_chunk_count": {"type": "integer"},
                "coverage_complete": {"type": "boolean"},
                "table_rows_preserved": {"type": ["boolean", "null"]},
            },
        ),
        _tool(
            "speak_text",
            "Synthesize AI-generated speech from text or a UTF-8 file. Long input is "
            "split into <=500-char chunks, verified for valid audio, and returned as "
            "WAV or MP3 artifacts.",
            {
                "text": {
                    "type": "string",
                    "description": "Text, workspace path, or artifact:<ref>.",
                },
                "language": {
                    "type": "string",
                    "description": "Language code for speech synthesis.",
                },
                "voice_id": {
                    "type": ["string", "null"],
                    "description": "Optional verified Bodhan voice name (e.g. 'Ishita', 'Amit').",
                },
                "voice_gender": {
                    "type": ["string", "null"],
                    "enum": ["female", "male", None],
                    "description": "Optional voice gender when voice_id is omitted.",
                },
                "output_format": {
                    "type": ["string", "null"],
                    "enum": ["wav", "mp3", None],
                    "description": "Output audio format: 'wav' (default) or 'mp3'.",
                },
            },
            ["text", "language"],
            {
                "language": {"type": "string"},
                "voice_id": {"type": "string"},
                "ai_generated": {"type": "boolean"},
                "character_count": {"type": "integer"},
                "audio_format": {"type": "string"},
                "audio_path": {"type": "string"},
                "audio_paths": {"type": "array", "items": {"type": "string"}},
                "duration_seconds": {"type": ["number", "null"]},
                "chunk_count": {"type": "integer"},
                "verification": _object_schema(
                    {
                        "nonempty_audio_chunks": {"type": "integer", "minimum": 0},
                        "complete": {"type": "boolean"},
                    }
                ),
            },
        ),
        _tool(
            "ocr_document",
            "Run OCR on an image or PDF. PDFs are processed page by page; omit page_number "
            "to process all pages. Preserves tables when table_format='markdown'.",
            {
                "file_path": {
                    "type": "string",
                    "description": "Workspace file path or artifact:<ref>.",
                },
                "page_number": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "Optional 1-based PDF page; omit for all pages.",
                },
                "language": {
                    "type": ["string", "null"],
                    "description": "Optional OCR language hint.",
                },
                "table_format": {
                    "type": "string",
                    "enum": ["markdown", "html"],
                    "description": "Table extraction format: 'markdown' (default) or 'html'.",
                },
            },
            ["file_path"],
            {
                "source_path": {"type": "string"},
                "page_number": {"type": ["integer", "null"], "minimum": 1},
                "language": {"type": ["string", "null"]},
                "pages_processed": {"type": "integer"},
                "total_pages": {"type": "integer"},
                "character_count": {"type": "integer"},
                "text": _TEXT_RESULT,
                "pages": {
                    "type": "array",
                    "items": _object_schema(
                        {
                            "page_number": {"type": "integer", "minimum": 1},
                            "text": {"type": "string"},
                            "preview": {"type": "string"},
                        },
                        ["page_number"],
                    ),
                },
                "coverage_complete": {"type": "boolean"},
            },
            [
                "source_path",
                "page_number",
                "language",
                "pages_processed",
                "total_pages",
                "character_count",
                "text",
                "pages",
                "coverage_complete",
            ],
        ),
        _tool(
            "chunk_media",
            "Split PCM WAV audio into <=30-second files or text/Markdown/subtitle files "
            "into translation-sized chunks. Returns chunk artifact references.",
            {
                "file_path": {
                    "type": "string",
                    "description": "Workspace file path or artifact:<ref>.",
                },
                "max_duration_s": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 30,
                    "description": "Optional chunk duration in seconds; defaults to 30.",
                },
            },
            ["file_path"],
            {
                "source_path": {"type": "string"},
                "chunk_count": {"type": "integer", "minimum": 0},
                "max_duration_seconds": {"type": "number", "minimum": 0, "maximum": 30},
                "max_characters_per_chunk": {"type": "integer", "minimum": 1},
                "chunks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            **_ARTIFACT_SCHEMA["properties"],
                            "index": {"type": "integer", "minimum": 1},
                            "duration_seconds": {"type": "number", "minimum": 0},
                            "characters": {"type": "integer", "minimum": 0},
                        },
                        "required": [*_ARTIFACT_SCHEMA["required"], "index"],
                        "additionalProperties": False,
                    },
                },
            },
            ["source_path", "chunk_count", "chunks"],
        ),
        _tool(
            "inspect_media",
            "Inspect file type, size, and available duration/page-count metadata without "
            "sending file contents to a model.",
            {
                "file_path": {
                    "type": "string",
                    "description": "Workspace file path or artifact:<ref>.",
                }
            },
            ["file_path"],
            {
                "path": {"type": "string"},
                "name": {"type": "string"},
                "file_type": {
                    "type": "string",
                    "enum": ["pdf", "audio", "video", "text", "image", "other"],
                },
                "extension": {"type": "string"},
                "size_bytes": {"type": "integer", "minimum": 0},
                "mime_type": {"type": "string"},
                "line_count": {"type": ["integer", "null"], "minimum": 0},
                "duration_seconds": {"type": ["number", "null"], "minimum": 0},
                "page_count": {"type": ["integer", "null"], "minimum": 0},
                "metadata_method": {"type": ["string", "null"]},
            },
        ),
        _tool(
            "detect_language",
            "Detect likely language and script candidates from text or a UTF-8 text file.",
            {
                "text_or_file": {
                    "type": "string",
                    "description": "Text, workspace path, or artifact:<ref>.",
                }
            },
            ["text_or_file"],
            {
                "script": {
                    "type": "string",
                    "enum": [
                        "DEVANAGARI",
                        "BENGALI",
                        "TAMIL",
                        "MALAYALAM",
                        "TELUGU",
                        "KANNADA",
                        "GUJARATI",
                        "ORIYA",
                        "PUNJABI",
                        "URDU",
                        "MEITEI",
                        "OL_CHIKI",
                        "ENGLISH",
                        "UNKNOWN",
                    ],
                },
                "candidates": {"type": "array", "items": {"type": "string"}},
                "ambiguous": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        ),
        _tool(
            "list_capabilities",
            "List supported languages, model prices, local chunk limits, and verified voices.",
            {},
            data_properties={
                "models": {
                    "type": "object",
                    "additionalProperties": _object_schema(
                        {
                            "name": {"type": "string"},
                            "pricing": _object_schema(
                                {
                                    "unit": {"type": "string"},
                                    "inr": {"type": "number", "minimum": 0},
                                }
                            ),
                        }
                    ),
                },
                "pricing": {
                    "type": "object",
                    "additionalProperties": _object_schema(
                        {
                            "unit": {"type": "string"},
                            "inr": {"type": "number", "minimum": 0},
                        }
                    ),
                },
                "supported_languages": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "default_voices": {
                    "type": "object",
                    "additionalProperties": _object_schema(
                        {"male": {"type": "string"}, "female": {"type": "string"}}
                    ),
                },
                "limits": _object_schema(
                    {
                        "max_audio_chunk_seconds": {"type": "number", "minimum": 0},
                        "translation_chunk_characters": {"type": "integer", "minimum": 1},
                        "tts_chunk_characters": {"type": "integer", "minimum": 1},
                        "pdf_pages": {"type": "string"},
                        "notes": {"type": "string"},
                    }
                ),
            },
        ),
    ]


_TOOLS = _build_tools()
_TOOLS_BY_NAME = {tool.name: tool for tool in _TOOLS}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return _TOOLS


def _required_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(
            ErrorCode.INVALID_ARGUMENT,
            f"`{key}` must be a non-empty string.",
            hint="Provide the required text or workspace file reference.",
        )
    return value


def _optional_string(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is not None and not isinstance(value, str):
        raise ToolError(
            ErrorCode.INVALID_ARGUMENT,
            f"`{key}` must be a string when provided.",
            hint="Omit the field or pass a language/voice identifier as text.",
        )
    return value


def _matches_json_type(value: Any, type_name: str) -> bool:
    if type_name == "null":
        return value is None
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return (
            isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
        )
    return False


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_matches_json_type(value, type_name) for type_name in expected_types):
            expected = " or ".join(expected_types)
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"`{path}` must be {expected}.",
                hint="Check the tool input schema and pass a value of the specified type.",
            )

    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(
            ErrorCode.INVALID_ARGUMENT,
            f"`{path}` must be one of {schema['enum']!r}.",
            hint="Choose one of the values listed in the tool input schema.",
        )

    if isinstance(value, int | float) and not isinstance(value, bool):
        bounds = (
            ("minimum", lambda actual, bound: actual < bound, "at least"),
            ("maximum", lambda actual, bound: actual > bound, "at most"),
            ("exclusiveMinimum", lambda actual, bound: actual <= bound, "greater than"),
            ("exclusiveMaximum", lambda actual, bound: actual >= bound, "less than"),
        )
        for key, violates, phrase in bounds:
            bound = schema.get(key)
            if bound is not None and violates(value, bound):
                raise ToolError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"`{path}` must be {phrase} {bound}.",
                    hint="Adjust the value to satisfy the tool input range.",
                )

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"`{path}` is missing required field(s): {', '.join(missing)}.",
                hint="Provide every field marked required by the tool input schema.",
            )
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                _validate_value(item, properties[key], f"{path}.{key}")
            elif additional is False:
                raise ToolError(
                    ErrorCode.INVALID_ARGUMENT,
                    f"`{path}` contains unsupported field `{key}`.",
                    hint="Remove fields not listed in the tool input schema.",
                )
            elif isinstance(additional, dict):
                _validate_value(item, additional, f"{path}.{key}")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], f"{path}[{index}]")


def _validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    _validate_value(arguments, schema, "arguments")


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    output = json.loads(json.dumps(result, ensure_ascii=False))
    if not output.get("ok"):
        return output
    artifacts = output.get("artifacts", [])
    text_artifact = next(
        (
            item
            for item in artifacts
            if item.get("kind")
            in {
                "text",
                "markdown",
                "subtitles",
                "transcript",
                "translation",
                "ocr",
            }
        ),
        None,
    )
    data = output.get("data", {})
    if isinstance(data, dict):
        for key in ("transcript", "translation", "text"):
            text = data.get(key)
            if isinstance(text, str) and len(text) > 500:
                data[key] = {
                    "preview": Workspace.preview(text),
                    "artifact_ref": f"artifact:{text_artifact['ref']}" if text_artifact else None,
                    "character_count": len(text),
                }
        pages = data.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict) and isinstance(page.get("text"), str):
                    page["preview"] = Workspace.preview(page.pop("text"))
    return output


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> tuple[list[TextContent], dict[str, Any]]:
    if not isinstance(arguments, dict):
        error = ToolError(
            ErrorCode.INVALID_ARGUMENT,
            "Tool arguments must be a JSON object.",
            hint="Call the tool with an object matching its input schema.",
        )
        output = {"ok": False, "error": error.to_dict()}
        return [TextContent(type="text", text=json.dumps(output))], output

    try:
        tool = _TOOLS_BY_NAME.get(name)
        if tool is None:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"Unknown tool name {name!r}.",
                hint="Call one of the tools returned by tools/list.",
            )
        _validate_arguments(arguments, tool.inputSchema)

        if name == "transcribe_audio":
            result = await asyncio.to_thread(
                transcribe_audio,
                workspace,
                _required_string(arguments, "file_path"),
                _optional_string(arguments, "language"),
                client,
                output_format=arguments.get("output_format", "text"),
                cache=cache,
            )
        elif name == "translate_text":
            result = await asyncio.to_thread(
                translate_text,
                workspace,
                _required_string(arguments, "text"),
                _required_string(arguments, "target_language"),
                _optional_string(arguments, "source_language"),
                client,
                cache=cache,
            )
        elif name == "speak_text":
            result = await asyncio.to_thread(
                speak_text,
                workspace,
                _required_string(arguments, "text"),
                _required_string(arguments, "language"),
                _optional_string(arguments, "voice_id"),
                client,
                voice_gender=_optional_string(arguments, "voice_gender"),
                output_format=arguments.get("output_format"),
                cache=cache,
            )
        elif name == "ocr_document":
            page_number = arguments.get("page_number")
            if page_number is not None and (
                isinstance(page_number, bool) or not isinstance(page_number, int)
            ):
                raise ToolError(
                    ErrorCode.INVALID_ARGUMENT,
                    "`page_number` must be a positive integer or omitted.",
                    hint="Use a 1-based page number or omit it to process every PDF page.",
                )
            result = await asyncio.to_thread(
                ocr_document,
                workspace,
                _required_string(arguments, "file_path"),
                page_number,
                _optional_string(arguments, "language"),
                client,
                table_format=arguments.get("table_format", "markdown"),
                cache=cache,
            )
        elif name == "chunk_media":
            result = await asyncio.to_thread(
                chunk_audio_manifest,
                workspace,
                _required_string(arguments, "file_path"),
                arguments.get("max_duration_s", 30.0),
            )
        elif name == "inspect_media":
            result = await asyncio.to_thread(
                inspect_media, workspace, _required_string(arguments, "file_path")
            )
        elif name == "detect_language":
            result = await asyncio.to_thread(
                detect_language, _required_string(arguments, "text_or_file"), workspace
            )
        elif name == "list_capabilities":
            result = list_capabilities()
        else:
            raise ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"Unknown tool name {name!r}.",
                hint="Call one of the tools returned by tools/list.",
            )

        usage = result.get("usage")
        if result.get("ok") and isinstance(usage, dict):
            ledger.record(
                model=str(usage.get("model", "unknown")),
                op=name,
                cost_inr=float(usage.get("cost_inr", 0.0)),
                cached=bool(usage.get("cached", False)),
                avoided_inr=float(usage.get("avoided_inr", 0.0)),
                latency_ms=int(usage.get("latency_ms", 0)),
                units=usage.get("units", {}),
            )
        output = _safe_result(result)
    except ToolError as exc:
        output = {"ok": False, "error": exc.to_dict()}
    except (KeyError, TypeError, ValueError) as exc:
        error = ToolError(
            ErrorCode.INVALID_ARGUMENT,
            f"Invalid arguments for {name}: {exc}",
            hint="Check the tool's input schema and provide values of the specified types.",
        )
        output = {"ok": False, "error": error.to_dict()}
    except Exception as exc:
        log.exception("Unhandled MCP tool failure for %s", name)
        error = ToolError(
            ErrorCode.INTERNAL,
            f"Tool {name!r} failed unexpectedly ({type(exc).__name__}).",
            hint="Check the server log for details, then retry or report the failure.",
        )
        output = {"ok": False, "error": error.to_dict()}

    return (
        [
            TextContent(
                type="text",
                text=json.dumps(output, ensure_ascii=False, indent=2),
            )
        ],
        output,
    )


async def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    global workspace, client, cache, ledger
    workspace = Workspace.from_env()
    client = BodhanClient()
    cache = DiskCache.from_env()
    ledger = CostLedger.from_env()

    from mcp.server.stdio import stdio_server

    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())