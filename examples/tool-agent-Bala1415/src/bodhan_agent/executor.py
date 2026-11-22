"""
Executor — dispatches tool calls from the planner to the actual tools.

Bridges the LLM planner with the MCP tool implementations.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from bodhan_mcp.client import BodhanClient, BodhanAPIError, BudgetExceededError
from bodhan_mcp.tools.transcribe import transcribe_audio, generate_srt
from bodhan_mcp.tools.translate import translate_text
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_mcp.tools.ocr import extract_text_ocr
from bodhan_mcp.helpers.detect_language import detect_language
from bodhan_mcp.helpers.inspect_media import inspect_media

logger = logging.getLogger(__name__)


class Executor:
    """
    Executes tool calls from the planner against the Bodhan API.

    Tracks results for the tracer and stores artifacts in the output directory.
    """

    def __init__(self, client: BodhanClient, output_dir: Path):
        self._client = client
        self._output_dir = output_dir
        self._artifacts_dir = output_dir / "artifacts"
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._results: dict[str, Any] = {}  # Store results by tool_call_id

    async def execute(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Execute a single tool call.

        Args:
            tool_name: Name of the tool to call.
            arguments: Arguments dict from the planner.

        Returns:
            dict with the tool's result.
        """
        start_time = time.time()

        try:
            if tool_name == "transcribe_audio":
                result = await transcribe_audio(
                    client=self._client,
                    file_path=arguments["file_path"],
                    language=arguments.get("language"),
                    output_dir=str(self._artifacts_dir),
                )

            elif tool_name == "translate_text":
                result = await translate_text(
                    client=self._client,
                    text=arguments["text"],
                    source_language=arguments["source_language"],
                    target_language=arguments["target_language"],
                )

            elif tool_name == "synthesize_speech":
                # Ensure output path is in artifacts dir
                output_path = arguments.get("output_path", "speech_output.wav")
                if not Path(output_path).is_absolute():
                    output_path = str(self._artifacts_dir / output_path)

                result = await synthesize_speech(
                    client=self._client,
                    text=arguments["text"],
                    language=arguments["language"],
                    voice=arguments.get("voice"),
                    output_path=output_path,
                    style=arguments.get("style", "default"),
                )

            elif tool_name == "extract_text_ocr":
                result = await extract_text_ocr(
                    client=self._client,
                    file_path=arguments["file_path"],
                    table_format=arguments.get("table_format", "markdown"),
                    output_dir=str(self._artifacts_dir),
                )

            elif tool_name == "detect_language":
                result = detect_language(arguments["text"])

            elif tool_name == "inspect_media":
                result = inspect_media(arguments["file_path"])

            elif tool_name == "task_complete":
                # This is a signal from the planner — not a real tool call
                result = {
                    "status": arguments.get("status", "completed"),
                    "summary": arguments.get("summary", ""),
                    "artifacts": arguments.get("artifacts", []),
                    "assumptions": arguments.get("assumptions", []),
                }

            else:
                result = {"error": True, "message": f"Unknown tool: {tool_name}"}

        except BodhanAPIError as e:
            result = {
                "error": True,
                "error_type": type(e).__name__,
                "message": str(e),
                "model": e.model,
                "status_code": e.status_code,
            }
        except BudgetExceededError as e:
            result = {
                "error": True,
                "error_type": "BudgetExceededError",
                "message": str(e),
            }
        except FileNotFoundError as e:
            result = {
                "error": True,
                "error_type": "FileNotFoundError",
                "message": str(e),
            }
        except ValueError as e:
            result = {
                "error": True,
                "error_type": "ValueError",
                "message": str(e),
            }
        except Exception as e:
            logger.exception(f"Unexpected error executing {tool_name}")
            result = {
                "error": True,
                "error_type": type(e).__name__,
                "message": str(e),
            }

        latency = time.time() - start_time
        result["_latency_seconds"] = round(latency, 3)
        return result

    def save_text_artifact(self, name: str, content: str, description: str = "") -> str:
        """Save a text artifact and return its path."""
        path = self._artifacts_dir / name
        path.write_text(content, encoding="utf-8")
        return str(path)
