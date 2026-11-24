"""Model Context Protocol (MCP) server implementation for Bodhan Indic AI models.

Supports:
1. Standard JSON-RPC 2.0 over stdio compatible with all off-the-shelf
   MCP clients (Claude Desktop, Cursor, MCP Inspector).
2. MCP Resources (bodhan://voices, bodhan://pricing, bodhan://languages).
3. MCP Prompts (summarize_audio, transcribe_and_subtitle).
4. Streamable HTTP / SSE transport with token authentication (--transport sse).
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

from .client.client import ASR_ONLY_LANGUAGES, SUPPORTED_LANGUAGES, TRANSLATE_LANGUAGES, VOICE_CATALOG
from .client.pricing import (
    OCR_INR_PER_PAGE,
    SPEAK_INR_PER_10K_CHARS,
    TRANSCRIBE_INR_PER_MINUTE,
    TRANSLATE_INR_PER_10K_TOKENS,
)
from .models import ToolError
from .tools import (
    bodhan_detect_language,
    bodhan_inspect_media,
    bodhan_list_voices,
    bodhan_make_subtitles,
    bodhan_ocr,
    bodhan_read_text,
    bodhan_speak,
    bodhan_transcribe,
    bodhan_translate,
    bodhan_verify_speech,
    bodhan_verify_translation,
)

SERVER_NAME = "bodhan-mcp-server"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

# Tool specifications with typed JSON schemas
TOOLS_REGISTRY: Dict[str, Dict[str, Any]] = {
    "bodhan_transcribe": {
        "name": "bodhan_transcribe",
        "description": (
            "Transcribe speech from an audio file into text using Bodhan Indic-Transcribe (₹0.10/min). "
            "Supports 25 Indian languages + English. Automatically chunks audio files exceeding 30 seconds "
            "and stitches transcripts with timestamps."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_file": {
                    "type": "string",
                    "description": "Path to the input audio file (WAV, MP3, etc.). Passed by file reference.",
                },
                "language": {
                    "type": "string",
                    "description": "Optional ISO language code hint (e.g. 'hi', 'ta', 'te', 'bn', 'en'). Omit for auto-detection.",
                },
            },
            "required": ["audio_file"],
        },
        "handler": lambda args: bodhan_transcribe(
            audio_file=args.get("audio_file"),
            language=args.get("language"),
        ).model_dump(),
    },
    "bodhan_translate": {
        "name": "bodhan_translate",
        "description": (
            "Translate text between English and 22 Indian languages using Bodhan Indic-Translate (₹0.20/10k tokens). "
            "Source language is auto-detected. Automatically splits long documents/paragraphs and preserves Markdown "
            "structure and tables."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to translate.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional path to a text/markdown file to translate.",
                },
                "target_language": {
                    "type": "string",
                    "description": "Required ISO code for the target language (e.g. 'ta', 'hi', 'bn', 'mr', 'te', 'en').",
                },
                "script": {
                    "type": "string",
                    "enum": ["native", "roman", "codemix"],
                    "default": "native",
                    "description": "Output script: 'native' (original script), 'roman' (Latin letters), or 'codemix' (Hinglish/Tanglish).",
                },
            },
            "required": ["target_language"],
        },
        "handler": lambda args: bodhan_translate(
            text=args.get("text"),
            file_path=args.get("file_path"),
            target_language=args.get("target_language"),
            script=args.get("script", "native"),
        ).model_dump(),
    },
    "bodhan_speak": {
        "name": "bodhan_speak",
        "description": (
            "Synthesize natural speech audio in 22 Indian languages + English using Bodhan Indic-Speak (₹6.00/10k chars). "
            "Outputs PCM16 24kHz mono WAV. Automatically chunks long text into sentence units and joins audio frames."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to speak aloud (provide either text or file_path).",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional path to a text/Markdown file to read aloud directly.",
                },
                "language": {
                    "type": "string",
                    "description": "ISO language code (e.g. 'hi', 'ta', 'bn', 'te', 'mr', 'en').",
                },
                "voice": {
                    "type": "string",
                    "description": "Optional voice name from the Bodhan voice catalog (e.g. 'Kavya', 'Anitha', 'Ishita', 'Amit'). Default chosen by language.",
                },
                "style": {
                    "type": "string",
                    "description": "Optional speaking style (e.g. 'news', 'TV style news', 'children\\'s stories', 'educational lecture', 'Customer Care').",
                },
                "output_file": {
                    "type": "string",
                    "description": "Destination path to save the generated .wav file. Default: speech_output.wav.",
                },
                "verify": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, back-transcribe each chunk via Indic-Transcribe and retry once on omission/repetition.",
                },
            },
        },
        "handler": lambda args: bodhan_speak(
            text=args.get("text"),
            file_path=args.get("file_path"),
            language=args.get("language"),
            voice=args.get("voice"),
            style=args.get("style"),
            output_file=args.get("output_file"),
            verify=bool(args.get("verify", False)),
        ).model_dump(),
    },
    "bodhan_ocr": {
        "name": "bodhan_ocr",
        "description": (
            "Extract text, Markdown and layout blocks from document images and multi-page PDFs using Bodhan Indic-OCR (₹0.20/page). "
            "Recognizes all Indian scripts and preserves table layouts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the document image (PNG, JPEG) or multi-page PDF.",
                },
                "table_format": {
                    "type": "string",
                    "enum": ["markdown", "html"],
                    "default": "markdown",
                    "description": "Format for extracted tables: 'markdown' or 'html'.",
                },
            },
            "required": ["file_path"],
        },
        "handler": lambda args: bodhan_ocr(
            file_path=args.get("file_path"),
            table_format=args.get("table_format", "markdown"),
        ).model_dump(),
    },
    "bodhan_inspect_media": {
        "name": "bodhan_inspect_media",
        "description": (
            "Inspect media files to get dimensions, duration, format, sample rate and metadata without loading binary content into LLM context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to inspect (audio, image, pdf, text, video).",
                },
            },
            "required": ["file_path"],
        },
        "handler": lambda args: bodhan_inspect_media(
            file_path=args.get("file_path")
        ).model_dump(),
    },
    "bodhan_detect_language": {
        "name": "bodhan_detect_language",
        "description": (
            "Detect Indian language and script from text or a file using Unicode character script distribution."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text sample to analyze.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional file path to analyze.",
                },
            },
        },
        "handler": lambda args: bodhan_detect_language(
            text=args.get("text"),
            file_path=args.get("file_path"),
        ).model_dump(),
    },
    "bodhan_list_voices": {
        "name": "bodhan_list_voices",
        "description": (
            "List available Indic-Speak voices, genders, and styles across all supported Indian languages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "Optional ISO language code to filter voices.",
                },
            },
        },
        "handler": lambda args: [v.model_dump() for v in bodhan_list_voices(args.get("language"))],
    },
    "bodhan_read_text": {
        "name": "bodhan_read_text",
        "description": (
            "Read a bounded slice of a text, Markdown, or transcript file marked untrusted with prompt-injection warnings. "
            "Keeps long documents out of LLM context unless requested."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the text/Markdown/transcript file."},
                "offset_chars": {"type": "integer", "default": 0, "description": "Character offset to start reading from."},
                "max_chars": {"type": "integer", "default": 4000, "description": "Maximum characters to return."},
            },
            "required": ["file_path"],
        },
        "handler": lambda args: bodhan_read_text(
            file_path=args.get("file_path"),
            offset_chars=args.get("offset_chars", 0),
            max_chars=args.get("max_chars", 4000),
        ),
    },
    "bodhan_verify_speech": {
        "name": "bodhan_verify_speech",
        "description": (
            "Back-transcribe an audio file with Indic-Transcribe and compare against expected text "
            "to detect silence, omissions, or repetitions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_file": {"type": "string", "description": "Path to the synthesized WAV audio file."},
                "expected_text": {"type": "string", "description": "The text that should have been spoken."},
                "language": {"type": "string", "description": "Optional ISO language code hint."},
            },
            "required": ["audio_file", "expected_text"],
        },
        "handler": lambda args: bodhan_verify_speech(
            audio_file=args.get("audio_file"),
            expected_text=args.get("expected_text"),
            language=args.get("language"),
        ),
    },
    "bodhan_verify_translation": {
        "name": "bodhan_verify_translation",
        "description": (
            "Verify a translation for target Unicode script match, number preservation, Markdown table integrity, "
            "length ratio, and optional paid back-translation to English."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_text": {"type": "string", "description": "Original source text."},
                "translated_text": {"type": "string", "description": "Translated text to verify."},
                "target_language": {"type": "string", "description": "Target ISO language code."},
                "script": {"type": "string", "enum": ["native", "roman", "codemix"], "default": "native"},
                "back_translate": {"type": "boolean", "default": False, "description": "If true, back-translate to English via Indic-Translate."},
            },
            "required": ["source_text", "translated_text", "target_language"],
        },
        "handler": lambda args: bodhan_verify_translation(
            source_text=args.get("source_text"),
            translated_text=args.get("translated_text"),
            target_language=args.get("target_language"),
            script=args.get("script", "native"),
            back_translate=bool(args.get("back_translate", False)),
        ),
    },
    "bodhan_make_subtitles": {
        "name": "bodhan_make_subtitles",
        "description": (
            "Format transcript text or timestamped segments into readable SRT or VTT subtitles "
            "(wrapped to max 42 chars x 2 lines per cue)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Full text to split into timed cues."},
                "segments": {
                    "type": "array",
                    "description": "Optional list of {start, end, text} segments.",
                    "items": {"type": "object"},
                },
                "duration_seconds": {"type": "number", "default": 10.0, "description": "Total clip duration when using plain text."},
                "fmt": {"type": "string", "enum": ["srt", "vtt"], "default": "srt"},
                "output_file": {"type": "string", "description": "Optional output file path to write subtitles."},
            },
        },
        "handler": lambda args: bodhan_make_subtitles(
            text=args.get("text"),
            segments=args.get("segments"),
            duration_seconds=args.get("duration_seconds", 10.0),
            fmt=args.get("fmt", "srt"),
            output_file=args.get("output_file"),
        ),
    },
}

# MCP Resources (Stretch Goal)
RESOURCES_REGISTRY: Dict[str, Dict[str, Any]] = {
    "bodhan://voices": {
        "uri": "bodhan://voices",
        "name": "Indic-Speak Voice Library",
        "description": "Catalog of 45 official voices and styles across 22 Indian languages and English.",
        "mimeType": "application/json",
        "reader": lambda: json.dumps(VOICE_CATALOG, indent=2, ensure_ascii=False),
    },
    "bodhan://pricing": {
        "uri": "bodhan://pricing",
        "name": "Bodhan Hosted Pricing Schedule",
        "description": "Official hosted model pricing in Indian Rupees (INR).",
        "mimeType": "application/json",
        "reader": lambda: json.dumps(
            {
                "indic-transcribe": {"rate": f"₹{TRANSCRIBE_INR_PER_MINUTE:.2f} per audio minute", "unit": "minute"},
                "indic-translate": {"rate": f"₹{TRANSLATE_INR_PER_10K_TOKENS:.2f} per 10,000 output tokens", "unit": "10k_tokens"},
                "indic-speak": {"rate": f"₹{SPEAK_INR_PER_10K_CHARS:.2f} per 10,000 input characters", "unit": "10k_chars"},
                "indic-ocr": {"rate": f"₹{OCR_INR_PER_PAGE:.2f} per image or page", "unit": "page"},
            },
            indent=2,
            ensure_ascii=False,
        ),
    },
    "bodhan://languages": {
        "uri": "bodhan://languages",
        "name": "Supported Indian Languages (Per-Model Matrix)",
        "description": "Supported ISO language codes and script variants broken down by Bodhan model.",
        "mimeType": "application/json",
        "reader": lambda: json.dumps(
            {
                "indic-transcribe": sorted(list(SUPPORTED_LANGUAGES)),
                "indic-translate": sorted(list(TRANSLATE_LANGUAGES)),
                "indic-speak": sorted(list(VOICE_CATALOG.keys())),
                "asr_only": sorted(list(ASR_ONLY_LANGUAGES)),
            },
            indent=2,
            ensure_ascii=False,
        ),
    },
}

# MCP Prompts (Stretch Goal)
PROMPTS_REGISTRY: Dict[str, Dict[str, Any]] = {
    "summarize_audio": {
        "name": "summarize_audio",
        "description": "Prompt template for transcribing an audio file, summarizing it in English, and reading it in an Indic language.",
        "arguments": [
            {"name": "audio_file", "description": "Path to input audio file", "required": True},
            {"name": "target_language", "description": "Target Indic language code (e.g. 'ta', 'hi')", "required": True},
        ],
    },
    "transcribe_and_subtitle": {
        "name": "transcribe_and_subtitle",
        "description": "Prompt template for creating translated English subtitles from an Indian-language voice recording.",
        "arguments": [
            {"name": "audio_file", "description": "Path to input audio file", "required": True},
        ],
    },
}


class MCPServer:
    """Standard JSON-RPC 2.0 stdio server conforming to Model Context Protocol with Resources and Prompts."""

    def __init__(self):
        self.tools = TOOLS_REGISTRY
        self.resources = RESOURCES_REGISTRY
        self.prompts = PROMPTS_REGISTRY

    def _validate_tool_arguments(self, tool_name: str, arguments: Any) -> Optional[str]:
        """Validate tool arguments against the registered inputSchema (required fields, types, enums)."""
        if not isinstance(arguments, dict):
            return "'arguments' must be a JSON object."
        schema = self.tools[tool_name].get("inputSchema", {})
        for req_field in schema.get("required", []):
            if req_field not in arguments or arguments[req_field] is None:
                return f"Missing required parameter '{req_field}' for tool '{tool_name}'."
        props = schema.get("properties", {})
        for key, val in arguments.items():
            if val is None or key not in props:
                continue
            prop_def = props[key]
            expected_type = prop_def.get("type")
            if expected_type == "string" and not isinstance(val, str):
                return f"Parameter '{key}' must be a string."
            elif expected_type == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
                return f"Parameter '{key}' must be an integer."
            elif expected_type == "number" and (not isinstance(val, (int, float)) or isinstance(val, bool)):
                return f"Parameter '{key}' must be a number."
            elif expected_type == "boolean" and not isinstance(val, bool):
                return f"Parameter '{key}' must be a boolean."
            elif expected_type == "array" and not isinstance(val, list):
                return f"Parameter '{key}' must be an array."
            if "enum" in prop_def and val not in prop_def["enum"]:
                return f"Parameter '{key}' must be one of {prop_def['enum']}, got '{val}'."
        return None

    def handle_request(self, request: Any) -> Optional[Dict[str, Any]]:
        """Process an incoming JSON-RPC request and return the response dictionary."""
        if not isinstance(request, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: expected JSON object"},
            }

        has_id = "id" in request
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params")
        if params is None:
            params = {}
        elif not isinstance(params, dict):
            if not has_id:
                return None
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Invalid params: expected JSON object"},
            }

        # Handle JSON-RPC notifications (no "id" field) — per spec, never reply to notifications
        if not has_id or method in ("notifications/initialized", "initialized"):
            return None

        # 1. Standard protocol initialization
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            }

        # 2. Ping
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        # 3. List tools
        if method == "tools/list":
            tool_definitions = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["inputSchema"],
                }
                for t in self.tools.values()
            ]
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tool_definitions},
            }

        # 4. Call tool
        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}

            if tool_name not in self.tools:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "error": f"Tool '{tool_name}' not found",
                                        "code": "TOOL_NOT_FOUND",
                                        "available_tools": list(self.tools.keys()),
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                        "isError": True,
                    },
                }

            validation_err = self._validate_tool_arguments(tool_name, arguments)
            if validation_err:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "error": validation_err,
                                        "code": "INVALID_ARGUMENTS",
                                        "suggestion": "Check the tool inputSchema via tools/list.",
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                        "isError": True,
                    },
                }

            try:
                handler = self.tools[tool_name]["handler"]
                output = handler(arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(output, ensure_ascii=False, indent=2),
                            }
                        ],
                        "isError": False,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "error": str(exc),
                                        "code": "TOOL_EXECUTION_ERROR",
                                        "suggestion": "Check file paths and argument types.",
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                        "isError": True,
                    },
                }

        # 5. List Resources (Stretch Goal)
        if method == "resources/list":
            resource_list = [
                {
                    "uri": r["uri"],
                    "name": r["name"],
                    "description": r["description"],
                    "mimeType": r["mimeType"],
                }
                for r in self.resources.values()
            ]
            return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": resource_list}}

        # 6. Read Resource (Stretch Goal)
        if method == "resources/read":
            uri = params.get("uri")
            if uri in self.resources:
                content = self.resources[uri]["reader"]()
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": self.resources[uri]["mimeType"],
                                "text": content,
                            }
                        ]
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Resource URI '{uri}' not found"},
            }

        # 7. List Prompts (Stretch Goal)
        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": list(self.prompts.values())}}

        # 8. Get Prompt (Stretch Goal)
        if method == "prompts/get":
            prompt_name = params.get("name")
            prompt_args = params.get("arguments") or {}
            if prompt_name not in self.prompts:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Prompt '{prompt_name}' not found"},
                }
            if prompt_name == "summarize_audio":
                audio_f = prompt_args.get("audio_file", "<audio_file>")
                tgt_lang = prompt_args.get("target_language", "hi")
                text = (
                    f"1. Inspect and transcribe '{audio_f}' with bodhan_transcribe.\n"
                    f"2. Write a concise English summary.\n"
                    f"3. Translate the summary into '{tgt_lang}' with bodhan_translate.\n"
                    f"4. Synthesize speech with bodhan_speak and verify with bodhan_verify_speech."
                )
            else:
                audio_f = prompt_args.get("audio_file", "<audio_file>")
                text = (
                    f"1. Transcribe '{audio_f}' with bodhan_transcribe.\n"
                    f"2. Translate the transcript into English with bodhan_translate.\n"
                    f"3. Format subtitles with bodhan_make_subtitles (fmt='srt')."
                )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "description": self.prompts[prompt_name]["description"],
                    "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
                },
            }

        # Method not found
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method '{method}' not found",
            },
        }

    def run_stdio(self) -> None:
        """Run the stdio message loop."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = self.handle_request(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error: Invalid JSON"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
            except Exception as e:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": f"Internal error: {str(e)}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()

    def run_sse(self, port: int = 8000, auth_token: Optional[str] = None) -> None:
        """Run streamable HTTP / SSE transport with token authentication (Stretch Goal)."""
        server_ref = self

        class MCPHTTPHandler(http.server.BaseHTTPRequestHandler):
            def _check_auth(self) -> bool:
                if not auth_token:
                    return True
                auth_hdr = self.headers.get("Authorization", "")
                return auth_hdr == f"Bearer {auth_token}"

            def do_GET(self):
                if not self._check_auth():
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"error": "Unauthorized"}\n')
                    return

                if self.path == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ok", "service": "bodhan-mcp-server"}\n')
                    return

                if self.path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    endpoint_msg = f"event: endpoint\ndata: /message\n\n"
                    self.wfile.write(endpoint_msg.encode("utf-8"))
                    self.wfile.flush()
                    try:
                        while True:
                            time.sleep(15)
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        pass
                    return

                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                if not self._check_auth():
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"error": "Unauthorized"}\n')
                    return

                if self.path == "/message":
                    content_len = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(content_len).decode("utf-8")
                    try:
                        req = json.loads(body)
                        resp = server_ref.handle_request(req)
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        if resp:
                            self.wfile.write(json.dumps(resp).encode("utf-8") + b"\n")
                    except Exception as e:
                        self.send_response(400)
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8") + b"\n")
                    return

                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                pass  # Suppress console clutter

        httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), MCPHTTPHandler)
        print(f"Bodhan MCP Server running on SSE/HTTP at http://localhost:{port} (Auth: {'Token protected' if auth_token else 'None'})")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.server_close()
