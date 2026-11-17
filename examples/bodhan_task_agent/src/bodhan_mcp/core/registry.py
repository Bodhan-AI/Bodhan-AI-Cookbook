from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

import mcp_types as types
from mcp.shared.exceptions import MCPError

from bodhan_mcp.core.base_tool import BaseTool
from bodhan_mcp.exceptions import BodhanToolError

logger = logging.getLogger(__name__)


def _definition(tool: BaseTool[Any, Any]) -> types.Tool:
    return types.Tool(
        name=tool.name,
        title=tool.title,
        description=tool.description,
        input_schema=tool.Input.model_json_schema(),
        output_schema=tool.Output.model_json_schema(),
        annotations=types.ToolAnnotations(
            read_only_hint=tool.read_only,
            destructive_hint=tool.destructive,
            idempotent_hint=tool.idempotent,
            open_world_hint=tool.open_world,
        ),
    )


def _error_result(error: BodhanToolError) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=error.to_json())],
        is_error=True,
    )


def _success_result(output: Any) -> types.CallToolResult:
    data = output.model_dump(mode="json")
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
        structured_content=data,
    )


class ToolRegistry:
    def __init__(self, tools: Iterable[BaseTool[Any, Any]]) -> None:
        self._tools: dict[str, BaseTool[Any, Any]] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Duplicate tool name: {tool.name}")
            self._tools[tool.name] = tool
        self._definitions = [_definition(tool) for tool in self._tools.values()]

    async def list_tools(self, _ctx: Any, _params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=self._definitions)

    async def call_tool(self, _ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool = self._tools.get(params.name)
        if tool is None:
            raise MCPError(
                types.INVALID_PARAMS,
                f"Unknown tool '{params.name}'. Available tools: {', '.join(sorted(self._tools))}.",
            )
        arguments = params.arguments if params.arguments is not None else {}
        try:
            output = await tool.execute(arguments)
            return _success_result(output)
        except BodhanToolError as error:
            return _error_result(error)
        except Exception:
            logger.exception("tool=%s failed outside its error handling", tool.name)
            return _error_result(BodhanToolError("internal_error", f"Tool '{tool.name}' failed unexpectedly."))
