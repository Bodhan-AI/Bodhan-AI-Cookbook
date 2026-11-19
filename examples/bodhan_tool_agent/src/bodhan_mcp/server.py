from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional

from bodhan_mcp.config import (
    MAX_ASR_WINDOW_SECONDS,
    MAX_TRANSLATE_CHUNK_CHARS,
    MAX_TTS_CHUNK_CHARS,
    PRICE_OCR_INR_PER_IMAGE,
    PRICE_SPEAK_INR_PER_10K_INPUT_CHARS,
    PRICE_TRANSCRIBE_INR_PER_MINUTE,
    PRICE_TRANSLATE_INR_PER_10K_OUTPUT_TOKENS,
)
from bodhan_mcp.languages import (
    SPEAKING_STYLES,
    SUPPORTED_LANGUAGES,
    UNSUPPORTED_LANGUAGES,
    VOICE_LIBRARY,
)
from bodhan_mcp.tools import BodhanToolSuite


MCP_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "inspect_media_and_files",
        "description": (
            "Inspect an input file or directory of files before processing. "
            "Returns media types (audio, video, pdf, image, text), audio/video duration in seconds, "
            "PDF page counts, image dimensions, detected language/script for text files, and "
            "prompt-injection security warnings. Always call this first on unknown input paths."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to an input file or a directory containing input files.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "extract_document_text",
        "description": (
            "Extract structured Markdown text (preserving headings, lists, Markdown tables, and LaTeX equations) "
            "from a scanned PDF, an image (.png, .jpg, .webp), or a folder of page images using Bodhan's Indic-OCR model. "
            "Automatically rasterizes multi-page PDFs to images (never reading embedded PDF text), drops repeated "
            "running headers/footers, rejoins split sentences across pages, and saves the Markdown output to `output_path`. "
            "Pass files by path reference, never base64."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the input PDF, image file, or directory of page images.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional path where the extracted Markdown (.md) file should be written.",
                },
                "page_start": {
                    "type": "integer",
                    "description": "Optional 1-based start page number for multi-page PDFs.",
                },
                "page_end": {
                    "type": "integer",
                    "description": "Optional 1-based end page number (inclusive) for multi-page PDFs.",
                },
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "transcribe_audio",
        "description": (
            "Transcribe speech from an audio or video file (.wav, .mp3, .mp4, .m4a, .flac, .ogg, .webm) "
            "using Bodhan's Indic-Transcribe model. Automatically extracts audio, splits audio longer than "
            "28 seconds on silence boundaries to satisfy the 30-second model window limit, detects the spoken "
            "Indian language and script, shifts timestamps across chunks, and writes the transcript to `output_path` "
            "in .txt, .srt, .vtt, or .json format."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the local audio or video file to transcribe.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional path where the transcript or subtitle file (.txt, .srt, .vtt, .json) should be written.",
                },
                "language": {
                    "type": "string",
                    "description": "ISO 639-1 language code (e.g., 'hi', 'ta', 'te', 'bn', 'mr', 'en') or 'auto' to auto-detect.",
                    "default": "auto",
                },
                "output_format": {
                    "type": "string",
                    "enum": ["txt", "srt", "vtt", "json", "md"],
                    "description": "Desired output file format: 'txt', 'srt' (subtitles), 'vtt', 'json', or 'md'.",
                    "default": "txt",
                },
                "model_variant": {
                    "type": "string",
                    "enum": ["indic-transcribe", "indic-transcribe-core", "indic-transcribe-flex"],
                    "description": "Bodhan ASR model variant to invoke.",
                    "default": "indic-transcribe",
                },
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "translate_text",
        "description": (
            "Translate text, Markdown documents (including tables and LaTeX), or timestamped subtitles (.srt/.vtt) "
            "into `target_language` using Bodhan's Indic-Translate model. Supports English ('en') and all 22 scheduled "
            "Indian languages ('hi', 'bn', 'ta', 'te', 'mr', 'gu', 'kn', 'ml', 'pa', 'or', 'as', 'ur', etc.). "
            "Automatically chunks long documents within the context window, preserves Markdown tables and numbers, "
            "and pivots through English when translating between two Indian languages. Returns an explicit "
            "UNSUPPORTED_LANGUAGE error for non-Indic foreign languages (e.g., French, Spanish, German)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_language": {
                    "type": "string",
                    "description": "Target language code or name (e.g., 'hi', 'ta', 'bn', 'te', 'mr', 'en').",
                },
                "input_path": {
                    "type": "string",
                    "description": "Path to the source text, Markdown (.md), subtitle (.srt/.vtt), or segments (.segments.json) file.",
                },
                "text": {
                    "type": "string",
                    "description": "Optional English source text string to translate if `input_path` is not used.",
                },
                "source_language": {
                    "type": "string",
                    "description": "Source language code (e.g., 'en', 'hi', 'te') or 'auto' to detect from script.",
                    "default": "auto",
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional path where the translated file (.md, .txt, .srt, .vtt) should be saved.",
                },
                "output_format": {
                    "type": "string",
                    "enum": ["md", "txt", "srt", "vtt"],
                    "description": "Optional output format override ('md', 'txt', 'srt', 'vtt').",
                },
            },
            "required": ["target_language"],
            "additionalProperties": False,
        },
    },
    {
        "name": "synthesize_speech",
        "description": (
            "Synthesize natural speech audio (.wav or .mp3) in an Indian language or English using Bodhan's "
            "Indic-Speak model. Automatically splits long text into short sentence chunks (<= 320 chars) to prevent "
            "repetition or omission, selects an authentic regional speaker voice from the fixed Indic-Speak library "
            "matching `language` and `gender` ('female' or 'male'), stitches audio chunks with natural pauses, "
            "and writes AI-generated disclosure metadata. Does not support voice cloning."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "Target language code or name for speech synthesis (e.g., 'ta', 'bn', 'hi', 'te', 'mr', 'en').",
                },
                "input_path": {
                    "type": "string",
                    "description": "Path to the text or Markdown file to read aloud.",
                },
                "text": {
                    "type": "string",
                    "description": "Optional text string to synthesize if `input_path` is not provided.",
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional output audio file path (.mp3 or .wav).",
                },
                "voice": {
                    "type": "string",
                    "description": "Optional specific Indic-Speak speaker name (e.g., 'Ishita', 'Sourav', 'Anitha', 'Arun', 'Kavya', 'Amit').",
                },
                "gender": {
                    "type": "string",
                    "enum": ["female", "male"],
                    "description": "Preferred speaker gender ('female' or 'male') if `voice` name is not specified.",
                    "default": "female",
                },
                "style": {
                    "type": "string",
                    "enum": ["narration", "explanatory", "news", "conversational", "neutral", "storytelling"],
                    "description": "Speaking style for Indic-Speak synthesis.",
                    "default": "narration",
                },
            },
            "required": ["language"],
            "additionalProperties": False,
        },
    },
    {
        "name": "verify_translation_quality",
        "description": (
            "Verify a translated file against its source file before finishing a task. "
            "Checks that the translation is non-empty, uses the expected target Unicode script, covers the full "
            "source text length, preserves numbers/digits and Markdown tables, and optionally runs a sample "
            "back-translation via Indic-Translate."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Path to the source text or Markdown file.",
                },
                "translated_path": {
                    "type": "string",
                    "description": "Path to the translated text, Markdown, or subtitle file.",
                },
                "target_language": {
                    "type": "string",
                    "description": "Expected target language code (e.g., 'mr', 'ta', 'hi', 'en').",
                },
                "run_back_translation": {
                    "type": "boolean",
                    "description": "Whether to run a sample back-translation check using Indic-Translate.",
                    "default": False,
                },
            },
            "required": ["source_path", "translated_path", "target_language"],
            "additionalProperties": False,
        },
    },
    {
        "name": "verify_audio_quality",
        "description": (
            "Verify a synthesized audio file (.wav or .mp3) before finishing a task. "
            "Checks audio duration, RMS energy, silence ratio, clipping, and back-transcribes with "
            "Indic-Transcribe to detect repetition, omission, or truncation against the expected text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Path to the generated audio file (.wav or .mp3) to verify.",
                },
                "expected_text_path": {
                    "type": "string",
                    "description": "Optional path to the source text file that was synthesized.",
                },
                "expected_text": {
                    "type": "string",
                    "description": "Optional expected text string if `expected_text_path` is not provided.",
                },
                "language": {
                    "type": "string",
                    "description": "Language code of the audio (e.g., 'ta', 'bn', 'hi') or 'auto'.",
                    "default": "auto",
                },
                "target_duration_sec": {
                    "type": "number",
                    "description": "Optional target duration in seconds (e.g., 120 for a two-minute summary).",
                },
                "run_back_transcription": {
                    "type": "boolean",
                    "description": "Whether to back-transcribe the audio with Indic-Transcribe to check accuracy.",
                    "default": True,
                },
            },
            "required": ["audio_path"],
            "additionalProperties": False,
        },
    },
]


MCP_RESOURCES: List[Dict[str, Any]] = [
    {
        "uri": "bodhan://capabilities/languages",
        "name": "Bodhan Supported Languages & Scripts",
        "description": "Complete list of the 22 scheduled Indian languages + English supported by Bodhan AI models, plus explicitly unsupported languages.",
        "mimeType": "application/json",
    },
    {
        "uri": "bodhan://capabilities/voices",
        "name": "Indic-Speak Voice Library",
        "description": "Fixed library of regional male and female speaker voices and speaking styles for Indic-Speak TTS.",
        "mimeType": "application/json",
    },
    {
        "uri": "bodhan://capabilities/pricing",
        "name": "Bodhan Model Pricing & Operational Limits",
        "description": "Per-model hosted API pricing in INR and operational chunking limits (ASR 30s window, TTS chunk size, translation context).",
        "mimeType": "application/json",
    },
]


MCP_PROMPTS: List[Dict[str, Any]] = [
    {
        "name": "multimodal_task_planner",
        "description": "Prompt template for planning a multi-step Indian language task using Bodhan MCP tools while enforcing Ground Rules.",
        "arguments": [
            {"name": "task", "description": "Plain-language task description", "required": True},
            {"name": "files_summary", "description": "Output of inspect_media_and_files", "required": True},
        ],
    },
    {
        "name": "translation_qa_verifier",
        "description": "Prompt template for reviewing translation and TTS verification results before completing a task.",
        "arguments": [
            {
                "name": "verification_json",
                "description": "JSON output from verify_translation_quality or verify_audio_quality",
                "required": True,
            },
        ],
    },
]


def get_openai_tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["inputSchema"],
            },
        }
        for tool in MCP_TOOL_DEFINITIONS
    ]


def read_mcp_resource(uri: str) -> Dict[str, Any]:
    if uri == "bodhan://capabilities/languages":
        return {
            "supported_languages": {
                code: {
                    "name": info.name,
                    "script": info.script,
                    "tts_production_tier": info.tts_production_tier,
                }
                for code, info in SUPPORTED_LANGUAGES.items()
            },
            "unsupported_languages_examples": UNSUPPORTED_LANGUAGES,
            "note": "Bodhan models support English (en) and the 22 scheduled Indian languages.",
        }
    if uri == "bodhan://capabilities/voices":
        return {
            "voice_library": VOICE_LIBRARY,
            "speaking_styles": SPEAKING_STYLES,
            "voice_cloning_supported": False,
            "ai_labeling_required": True,
        }
    if uri == "bodhan://capabilities/pricing":
        return {
            "currency": "INR",
            "models": {
                "indic-transcribe": {
                    "price_inr": PRICE_TRANSCRIBE_INR_PER_MINUTE,
                    "unit": "per_input_audio_minute",
                    "max_window_seconds": MAX_ASR_WINDOW_SECONDS,
                },
                "indic-translate": {
                    "price_inr": PRICE_TRANSLATE_INR_PER_10K_OUTPUT_TOKENS,
                    "unit": "per_10000_output_tokens",
                    "max_chunk_chars": MAX_TRANSLATE_CHUNK_CHARS,
                },
                "indic-speak": {
                    "price_inr": PRICE_SPEAK_INR_PER_10K_INPUT_CHARS,
                    "unit": "per_10000_input_characters",
                    "max_chunk_chars": MAX_TTS_CHUNK_CHARS,
                },
                "indic-ocr": {
                    "price_inr": PRICE_OCR_INR_PER_IMAGE,
                    "unit": "per_image_or_page",
                },
            },
        }
    raise ValueError(f"Unknown MCP resource URI: '{uri}'")


def get_mcp_prompt(name: str, arguments: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    args = arguments or {}
    if name == "multimodal_task_planner":
        task_str = args.get("task", "")
        files_str = args.get("files_summary", "{}")
        return {
            "description": "Plan a multi-step Indian language task using Bodhan MCP tools.",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"Task: {task_str}\n"
                            f"Input Files Inspection: {files_str}\n\n"
                            "Rules:\n"
                            "1. Every ASR, translation, TTS, and OCR call must use a Bodhan tool.\n"
                            "2. Never write Indian-language text directly; write English and call `translate_text`.\n"
                            "3. Always verify generated translations and audio before concluding."
                        ),
                    },
                }
            ],
        }
    if name == "translation_qa_verifier":
        ver_str = args.get("verification_json", "{}")
        return {
            "description": "Evaluate translation and audio verification results.",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": f"Review the following verification report and decide whether to retry or finalize:\n{ver_str}",
                    },
                }
            ],
        }
    raise ValueError(f"Unknown MCP prompt name: '{name}'")


class BodhanMCPServer:
    def __init__(self, tool_suite: Optional[BodhanToolSuite] = None) -> None:
        self.tool_suite = tool_suite or BodhanToolSuite()
        self._dispatch_map: Dict[str, Callable[..., Dict[str, Any]]] = {
            "inspect_media_and_files": self.tool_suite.inspect_media_and_files,
            "extract_document_text": self.tool_suite.extract_document_text,
            "transcribe_audio": self.tool_suite.transcribe_audio,
            "translate_text": self.tool_suite.translate_text,
            "synthesize_speech": self.tool_suite.synthesize_speech,
            "verify_translation_quality": self.tool_suite.verify_translation_quality,
            "verify_audio_quality": self.tool_suite.verify_audio_quality,
        }

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if name not in self._dispatch_map:
            return {
                "status": "error",
                "error_code": "UNKNOWN_TOOL",
                "message": f"Unknown tool '{name}'.",
                "suggestion": f"Available tools: {', '.join(self._dispatch_map.keys())}.",
            }

        args = dict(arguments or {})
        schema = next((t["inputSchema"] for t in MCP_TOOL_DEFINITIONS if t["name"] == name), {})
        required_fields = schema.get("required", [])
        missing = [req for req in required_fields if req not in args or args[req] is None or args[req] == ""]
        if missing:
            return {
                "status": "error",
                "error_code": "INVALID_TOOL_ARGUMENTS",
                "message": f"Missing required argument(s) for tool '{name}': {missing}.",
                "suggestion": f"Provide all required parameters: {required_fields}.",
            }

        allowed_props = set(schema.get("properties", {}).keys())
        unknown_props = [k for k in args.keys() if allowed_props and k not in allowed_props]
        if unknown_props:
            return {
                "status": "error",
                "error_code": "UNKNOWN_TOOL_ARGUMENTS",
                "message": f"Unknown argument(s) passed to tool '{name}': {unknown_props}.",
                "suggestion": f"Valid parameters for '{name}' are: {sorted(allowed_props)}.",
            }

        try:
            func = self._dispatch_map[name]
            return func(**args)
        except TypeError as exc:
            return {
                "status": "error",
                "error_code": "INVALID_ARGUMENT_TYPE",
                "message": f"Invalid argument type for tool '{name}': {exc}",
                "suggestion": f"Check parameter types in '{name}' schema.",
            }
        except Exception as exc:
            return {
                "status": "error",
                "error_code": "INTERNAL_TOOL_ERROR",
                "message": f"Unexpected error in tool '{name}': {exc}",
                "suggestion": "Verify input file paths and environment configuration.",
            }

    def handle_jsonrpc_message(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        req_id = request.get("id")
        method = str(request.get("method", ""))
        params = request.get("params") or {}

        if req_id is None and method.startswith("notifications/"):
            return None

        try:
            if method == "initialize":
                client_proto = params.get("protocolVersion", "2024-11-05")
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": client_proto,
                        "capabilities": {
                            "tools": {"listChanged": False},
                            "resources": {"subscribe": False, "listChanged": False},
                            "prompts": {"listChanged": False},
                        },
                        "serverInfo": {
                            "name": "bodhan-mcp",
                            "version": "0.1.0",
                        },
                    },
                }

            if method == "ping":
                return {"jsonrpc": "2.0", "id": req_id, "result": {}}

            if method == "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"tools": MCP_TOOL_DEFINITIONS},
                }

            if method == "tools/call":
                tool_name = str(params.get("name", ""))
                tool_args = params.get("arguments") or {}
                res_dict = self.call_tool(tool_name, tool_args)
                is_err = res_dict.get("status") == "error"
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(res_dict, ensure_ascii=False, indent=2),
                            }
                        ],
                        "structuredContent": res_dict,
                        "isError": is_err,
                    },
                }

            if method == "resources/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"resources": MCP_RESOURCES},
                }

            if method == "resources/read":
                uri = str(params.get("uri", ""))
                payload = read_mcp_resource(uri)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": json.dumps(payload, ensure_ascii=False, indent=2),
                            }
                        ]
                    },
                }

            if method == "prompts/list":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"prompts": MCP_PROMPTS},
                }

            if method == "prompts/get":
                p_name = str(params.get("name", ""))
                p_args = params.get("arguments") or {}
                prompt_res = get_mcp_prompt(p_name, p_args)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": prompt_res,
                }

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: '{method}'",
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": str(exc),
                },
            }

    def run_stdio(self) -> None:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
                sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
                continue

            resp = self.handle_jsonrpc_message(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()

    def handle_http_request(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        body: str = "",
        expected_token: Optional[str] = None,
    ) -> tuple[int, Dict[str, str], bytes]:
        if expected_token:
            auth_val = headers.get("Authorization") or headers.get("authorization") or ""
            if auth_val != f"Bearer {expected_token}":
                return (
                    401,
                    {"Content-Type": "application/json"},
                    b'{"error": "Unauthorized: invalid or missing Bearer token"}',
                )

        if method.upper() == "GET":
            if path in ("/health", "/mcp"):
                info = {"status": "ok", "server": "bodhan-mcp", "transport": "streamable-http"}
                return 200, {"Content-Type": "application/json"}, json.dumps(info).encode("utf-8")
            return 404, {"Content-Type": "application/json"}, b'{"error": "Not found"}'

        if method.upper() == "POST":
            try:
                req_obj = json.loads(body)
                resp_obj = self.handle_jsonrpc_message(req_obj)
                if resp_obj is None:
                    return 202, {}, b""
                payload_bytes = json.dumps(resp_obj, ensure_ascii=False).encode("utf-8")
                return (
                    200,
                    {"Content-Type": "application/json", "Content-Length": str(len(payload_bytes))},
                    payload_bytes,
                )
            except Exception as exc:
                err = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}).encode(
                    "utf-8"
                )
                return 400, {"Content-Type": "application/json"}, err

        return 405, {"Content-Type": "application/json"}, b'{"error": "Method not allowed"}'

    def run_http(self, host: str = "127.0.0.1", port: int = 8000, auth_token: Optional[str] = None) -> None:
        server_ref = self
        expected_token = auth_token or os.environ.get("BODHAN_MCP_AUTH_TOKEN")

        class MCPHTTPHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                hdrs = {k: v for k, v in self.headers.items()}
                code, resp_hdrs, data = server_ref.handle_http_request(
                    "GET", self.path, hdrs, expected_token=expected_token
                )
                self.send_response(code)
                for hk, hv in resp_hdrs.items():
                    self.send_header(hk, hv)
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def do_POST(self) -> None:
                hdrs = {k: v for k, v in self.headers.items()}
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length).decode("utf-8")
                code, resp_hdrs, data = server_ref.handle_http_request(
                    "POST", self.path, hdrs, body=raw_body, expected_token=expected_token
                )
                self.send_response(code)
                for hk, hv in resp_hdrs.items():
                    self.send_header(hk, hv)
                self.end_headers()
                if data:
                    self.wfile.write(data)

        httpd = HTTPServer((host, port), MCPHTTPHandler)
        try:
            httpd.serve_forever()
        finally:
            httpd.server_close()
