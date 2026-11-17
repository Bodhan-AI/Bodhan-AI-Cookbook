"""Pydantic v2 input schemas for all Bodhan MCP tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class TranscribeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    file_path: str
    language: str | None = None
    output_format: Literal["text", "srt", "vtt"] = "text"


class TranslateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str | None = None
    file_path: str | None = None
    source_lang: str | None = None
    target_lang: str
    preserve_markdown: bool = True


class SynthesizeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str
    language: str
    voice: str | None = None
    gender: Literal["female", "male"] = "female"
    style: str | None = None
    output_path: str | None = None


class OCRInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    file_path: str
    table_format: Literal["markdown", "html"] = "markdown"


class InspectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    file_path: str


class DetectLanguageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str | None = None
    file_path: str | None = None


class ListVoicesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    language: str | None = None
