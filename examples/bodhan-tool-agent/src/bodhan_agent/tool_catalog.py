
from __future__ import annotations

import os
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from bodhan_mcp.cache import price_table
from bodhan_mcp.client import KEY_ENV
from bodhan_mcp.languages import ALL_SPEAK_VOICES, DEFAULT_VOICES, SUPPORTED_LANGUAGES
from bodhan_mcp.tools.speak import MAX_TTS_CHARS
from bodhan_mcp.tools.transcribe import MAX_AUDIO_WINDOW_SECONDS
from bodhan_mcp.tools.translate import MAX_TRANSLATE_CHARS

_AUDIO_SUFFIXES = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"})
_VIDEO_SUFFIXES = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"})
_IMAGE_SUFFIXES = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"})
_TEXT_SUFFIXES = frozenset({".markdown", ".md", ".srt", ".txt", ".vtt"})
_PDF_SUFFIXES = frozenset({".pdf"})
_MODEL_KEY_ENV = {**KEY_ENV, "llm": "LLM_API_KEY"}


@dataclass(frozen=True)
class AgentToolSpec:
    """Executable task-agent operation and its machine-readable contract."""

    name: str
    model: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    input_suffixes: frozenset[str]
    output_types: dict[str, str]
    language_fields: frozenset[str] = frozenset()
    required_models: tuple[str, ...] = ()

    @property
    def fields(self) -> frozenset[str]:
        return frozenset({"tool", *self.input_schema["properties"]})

    def as_dict(self, env: Mapping[str, str]) -> dict[str, Any]:
        missing_models = [
            model
            for model in self.required_models
            if not (env.get(_MODEL_KEY_ENV[model]) or "").strip()
        ]
        return {
            "name": self.name,
            "model": self.model,
            "required_models": list(self.required_models),
            "description": self.description,
            "input_schema": deepcopy(self.input_schema),
            "output_schema": deepcopy(self.output_schema),
            "input_file_extensions": sorted(self.input_suffixes),
            "output_types": dict(self.output_types),
            "limits": _tool_limits(self.name),
            "available": not missing_models,
            "availability_reason": (
                None
                if not missing_models
                else "Configure "
                + ", ".join(_MODEL_KEY_ENV[model] for model in missing_models)
                + " to execute this operation."
            ),
        }


_TEXT_OR_FILE = {
    "type": "string",
    "description": "UTF-8 text, a workspace file path, or artifact:<ref>.",
}
_FILE_REFERENCE = {
    "type": "string",
    "description": "Workspace file path or artifact:<ref>; never pass media bytes inline.",
}
_LANGUAGE = {"type": ["string", "null"], "description": "Optional configured language code."}

AGENT_TOOLS: dict[str, AgentToolSpec] = {
    "transcribe": AgentToolSpec(
        name="transcribe",
        model="transcribe",
        description=(
            "Transcribe audio or extract+transcribe a video's audio track. Automatically "
            "segments media into <=30s windows; pass output_format='srt' for subtitles."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "file_path": _FILE_REFERENCE,
                "language": _LANGUAGE,
                "output_format": {
                    "type": "string",
                    "enum": ["text", "srt"],
                    "description": "Optional output format: 'text' (default) or 'srt'.",
                },
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "transcript": {"type": "string"},
                "language_detected": {"type": ["string", "null"]},
                "duration_seconds": {"type": ["number", "null"]},
                "chunk_count": {"type": "integer"},
                "output_format": {"type": "string"},
                "coverage_complete": {"type": "boolean"},
            },
            "required": ["transcript", "chunk_count", "coverage_complete"],
        },
        input_suffixes=_AUDIO_SUFFIXES | _VIDEO_SUFFIXES,
        output_types={
            "transcript": "text",
            "language_detected": "string",
            "duration_seconds": "number",
            "chunk_count": "integer",
            "output_format": "string",
            "coverage_complete": "boolean",
        },
        language_fields=frozenset({"language"}),
        required_models=("transcribe",),
    ),
    "translate": AgentToolSpec(
        name="translate",
        model="translate",
        description=(
            "Translate text, a UTF-8 file (.txt, .md, .srt, .vtt), or prior step text. "
            "Chunks long inputs safely while preserving SRT timestamps and Markdown tables."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": _TEXT_OR_FILE,
                "target_language": {
                    "type": "string",
                    "description": "Required configured target language code or name.",
                },
                "source_language": _LANGUAGE,
                "source_key": {
                    "type": "string",
                    "description": (
                        "Legacy key name from an earlier step; in version-1 plans prefer passing ",
                        "{\"$ref\":{\"step_id\":\"...\",\"output\":\"...\"}} intext."
                    ),
                },
            },
            "required": ["target_language"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "translation": {"type": "string"},
                "target_language": {"type": "string"},
                "source_language": {"type": ["string", "null"]},
                "source_character_count": {"type": "integer"},
                "chunk_count": {"type": "integer"},
                "translated_chunk_count": {"type": "integer"},
                "coverage_complete": {"type": "boolean"},
            },
            "required": [
                "translation",
                "chunk_count",
                "translated_chunk_count",
                "coverage_complete",
            ],
        },
        input_suffixes=_TEXT_SUFFIXES,
        output_types={
            "translation": "text",
            "target_language": "string",
            "source_language": "string",
            "source_character_count": "integer",
            "chunk_count": "integer",
            "translated_chunk_count": "integer",
            "coverage_complete": "boolean",
        },
        language_fields=frozenset({"target_language", "source_language"}),
        required_models=("translate",),
    ),
    "speak": AgentToolSpec(
        name="speak",
        model="speak",
        description=(
            "Synthesize AI-generated speech from text or a UTF-8 file, verify it with "
            "back-transcription, and write WAV or MP3 audio. Select voice_id or voice_gender."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": _TEXT_OR_FILE,
                "language": {
                    "type": "string",
                    "description": "Required configured language code or name.",
                },
                "voice_id": {
                    "type": ["string", "null"],
                    "description": "Optional verified Bodhan voice name (e.g. 'Ishita', 'Anitha').",
                },
                "voice_gender": {
                    "type": ["string", "null"],
                    "enum": ["female", "male", None],
                    "description": "Optional voice gender ('female' or 'male').",
                },
                "output_format": {
                    "type": ["string", "null"],
                    "enum": ["wav", "mp3", None],
                    "description": "Optional output audio format: 'wav' or 'mp3'.",
                },
                "source_key": {
                    "type": "string",
                    "description": (
                        "Legacy key name from an earlier step; in version-1 plans prefer passing",
                        " {\"$ref\":{\"step_id\":\"...\",\"output\":\"...\"}} intext."
                    ),
                },
            },
            "required": ["language"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "audio_path": {"type": "string"},
                "audio_paths": {"type": "array", "items": {"type": "string"}},
                "language": {"type": "string"},
                "voice_id": {"type": "string"},
                "audio_format": {"type": "string"},
                "duration_seconds": {"type": ["number", "null"]},
                "chunk_count": {"type": "integer"},
                "verification": {"type": "object"},
            },
            "required": ["audio_paths", "language", "chunk_count", "verification"],
        },
        input_suffixes=_TEXT_SUFFIXES,
        output_types={
            "audio_path": "audio",
            "audio_paths": "audio_list",
            "language": "string",
            "voice_id": "string",
            "audio_format": "string",
            "duration_seconds": "number",
            "chunk_count": "integer",
            "verification": "object",
        },
        language_fields=frozenset({"language"}),
        required_models=("speak", "transcribe"),
    ),
    "ocr": AgentToolSpec(
        name="ocr",
        model="ocr",
        description=(
            "Extract text (and Markdown tables via table_format='markdown') from an image "
            "or PDF. Omitting page_number processes all pages."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "file_path": _FILE_REFERENCE,
                "page_number": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "description": "Optional one-based PDF page; omit to process all pages.",
                },
                "language": _LANGUAGE,
                "table_format": {
                    "type": "string",
                    "enum": ["markdown", "html"],
                    "description": "Optional table format: 'markdown' (default) or 'html'.",
                },
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "pages_processed": {"type": "integer"},
                "total_pages": {"type": "integer"},
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page_number": {"type": "integer"},
                            "text": {"type": "string"},
                        },
                    },
                },
                "coverage_complete": {"type": "boolean"},
            },
            "required": ["text", "pages_processed", "total_pages", "coverage_complete"],
        },
        input_suffixes=_IMAGE_SUFFIXES | _PDF_SUFFIXES,
        output_types={
            "text": "text",
            "pages_processed": "integer",
            "total_pages": "integer",
            "pages": "array",
            "coverage_complete": "boolean",
        },
        language_fields=frozenset({"language"}),
        required_models=("ocr",),
    ),
    "detect_language": AgentToolSpec(
        name="detect_language",
        model="local",
        description=(
            "Detect Unicode script and likely language candidates from text or a UTF-8 file. "
            "Use after transcribing or OCR-ing text of unknown language."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text_or_file": _TEXT_OR_FILE,
            },
            "required": ["text_or_file"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "script": {"type": "string"},
                "candidates": {"type": "array", "items": {"type": "string"}},
                "ambiguous": {"type": "boolean"},
                "confidence": {"type": "number"},
            },
            "required": ["script", "candidates", "ambiguous", "confidence"],
        },
        input_suffixes=_TEXT_SUFFIXES,
        output_types={
            "script": "string",
            "candidates": "array",
            "ambiguous": "boolean",
            "confidence": "number",
        },
        required_models=(),
    ),
    "summarize_english": AgentToolSpec(
        name="summarize_english",
        model="llm",
        description=(
            "Write an English-only summary of text produced by an earlier Bodhan OCR or "
            "transcription step. Use max_words to target spoken duration (~130 words/minute, "
            "so ~260 words for a 2-minute audio summary). Never output Indian-language text."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "UTF-8 text or text output from an earlier step.",
                },
                "max_words": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "description": "Optional maximum summary length; defaults to 220 words.",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "word_count": {"type": "integer"},
                "language": {"type": "string", "enum": ["en"]},
            },
            "required": ["summary", "word_count", "language"],
        },
        input_suffixes=_TEXT_SUFFIXES,
        output_types={
            "summary": "text",
            "word_count": "integer",
            "language": "string",
        },
        required_models=("llm",),
    ),
}

_FEATURES = {
    "audio_transcription": {
        "implemented": True,
        "tool": "transcribe",
        "required_models": ["transcribe"],
    },
    "automatic_audio_windowing": {
        "implemented": True,
        "tool": "transcribe",
        "required_models": ["transcribe"],
        "maximum_window_seconds": MAX_AUDIO_WINDOW_SECONDS,
    },
    "text_translation": {
        "implemented": True,
        "tool": "translate",
        "required_models": ["translate"],
    },
    "automatic_translation_chunking": {
        "implemented": True,
        "tool": "translate",
        "required_models": ["translate"],
        "maximum_chunk_characters": MAX_TRANSLATE_CHARS,
    },
    "speech_synthesis": {
        "implemented": True,
        "tool": "speak",
        "required_models": ["speak", "transcribe"],
    },
    "automatic_speech_chunking": {
        "implemented": True,
        "tool": "speak",
        "required_models": ["speak", "transcribe"],
        "maximum_chunk_characters": MAX_TTS_CHARS,
    },
    "speech_back_transcription_verification": {
        "implemented": True,
        "tool": "speak",
        "required_models": ["speak", "transcribe"],
    },
    "image_and_multipage_pdf_ocr": {
        "implemented": True,
        "tool": "ocr",
        "required_models": ["ocr"],
    },
    "duration_targeted_speech": {
        "implemented": True,
        "tools": ["summarize_english", "translate", "speak"],
        "required_models": ["llm", "translate", "speak", "transcribe"],
    },
    "task_level_summarization": {
        "implemented": True,
        "tool": "summarize_english",
        "required_models": ["llm"],
    },
    "timestamped_transcription": {
        "implemented": True,
        "tool": "transcribe",
        "required_models": ["transcribe"],
    },
    "structured_table_ocr": {
        "implemented": True,
        "tool": "ocr",
        "required_models": ["ocr"],
    },
    "spoken_language_identification": {
        "implemented": True,
        "tools": ["transcribe", "detect_language"],
        "required_models": ["transcribe"],
    },
    "video_audio_extraction": {
        "implemented": True,
        "tool": "transcribe",
        "required_models": ["transcribe"],
    },
    "deployment_verified_voice_catalog": {
        "implemented": True,
        "tool": "speak",
        "required_models": ["speak", "transcribe"],
    },
}


def _tool_limits(name: str) -> dict[str, Any]:
    if name == "transcribe":
        return {"maximum_audio_window_seconds": MAX_AUDIO_WINDOW_SECONDS}
    if name == "translate":
        return {"maximum_chunk_characters": MAX_TRANSLATE_CHARS}
    if name == "speak":
        return {"maximum_chunk_characters": MAX_TTS_CHARS}
    if name == "ocr":
        return {"pdf_pages": "all pages unless page_number is supplied"}
    return {}


def _feature_catalog(env: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {}
    for name, metadata in _FEATURES.items():
        feature = deepcopy(metadata)
        required_models = feature.get("required_models", [])
        missing_models = [
            model
            for model in required_models
            if not (env.get(_MODEL_KEY_ENV[model]) or "").strip()
        ]
        feature["available"] = feature["implemented"] and not missing_models
        if missing_models:
            feature["availability_reason"] = (
                "Configure "
                + ", ".join(_MODEL_KEY_ENV[model] for model in missing_models)
                + "."
            )
        features[name] = feature
    return features


def build_tool_catalog(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return the current task-agent tools and deployment configuration, without secrets."""
    environment = os.environ if env is None else env
    prices = price_table()
    models = {
        model: {
            "pricing": prices[model],
            "credential_configured": bool((environment.get(KEY_ENV[model]) or "").strip()),
            "credential_env": KEY_ENV[model],
        }
        for model in ("transcribe", "translate", "speak", "ocr")
    }
    models["llm"] = {
        "pricing": None,
        "credential_configured": bool(
            (environment.get(_MODEL_KEY_ENV["llm"]) or "").strip()
        ),
        "credential_env": _MODEL_KEY_ENV["llm"],
    }
    return {
        "version": 1,
        "scope": "task_agent_executable_tools",
        "tools": [spec.as_dict(environment) for spec in AGENT_TOOLS.values()],
        "models": models,
        "supported_languages": sorted(SUPPORTED_LANGUAGES),
        "default_voices": deepcopy(DEFAULT_VOICES),
        "verified_voices": sorted(ALL_SPEAK_VOICES),
        "features": _feature_catalog(environment),
    }
