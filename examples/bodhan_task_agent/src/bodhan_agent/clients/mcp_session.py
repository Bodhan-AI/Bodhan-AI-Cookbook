from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AsyncExitStack
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, REQUEST_TIMEOUT

from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.mcp import McpError, McpResult, McpToolSpec

SERVER_MODULE = "bodhan_mcp"
DEFAULT_TOOL_DEADLINE_S = 240.0
CONNECT_TIMEOUT_S = 30.0
STDERR_TAIL_CHARS = 600
UNCERTAIN_SERVER_CODES: frozenset[str] = frozenset({"timeout", "tool_deadline_exceeded", "upstream_error"})
_TRANSPORT_ERRORS = (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream, OSError)


class McpUnavailable(Exception):
    pass


def _tool_deadline(env: Mapping[str, str]) -> float:
    try:
        return max(1.0, float(env.get("BODHAN_TOOL_DEADLINE_S", DEFAULT_TOOL_DEADLINE_S)))
    except ValueError:
        return DEFAULT_TOOL_DEADLINE_S


def _text(result: Any) -> str:
    parts = [getattr(item, "text", "") for item in getattr(result, "content", None) or []]
    return "\n".join(part for part in parts if isinstance(part, str))


def _error_payload(text: str, structured: Any) -> dict[str, Any] | None:
    if isinstance(structured, dict) and isinstance(structured.get("error"), dict):
        return structured["error"]
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else None


def _server_error(text: str, structured: Any = None) -> McpError:
    error = _error_payload(text, structured)
    if error is None or not isinstance(error.get("code"), str):
        return McpError(
            code="tool_error", message=text.strip() or "The tool reported an error without details.", uncertain=True
        )
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    usage = details.get("usage") if isinstance(details.get("usage"), dict) else None
    reported = usage.get("billing_uncertain") if usage is not None else None
    uncertain = reported if isinstance(reported, bool) else error["code"] in UNCERTAIN_SERVER_CODES
    status = error.get("status")
    hint = error.get("hint")
    return McpError(
        code=error["code"],
        message=str(error.get("message") or error["code"]),
        status=status if isinstance(status, int) else None,
        hint=hint if isinstance(hint, str) and hint else None,
        retryable=error.get("retryable") is True,
        details=details,
        uncertain=uncertain,
    )


def _success_data(result: Any) -> dict[str, Any] | None:
    data = getattr(result, "structured_content", None)
    if isinstance(data, dict):
        return data
    try:
        parsed = json.loads(_text(result))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _specs(listed: Any) -> list[McpToolSpec]:
    return [
        McpToolSpec(
            name=tool.name,
            description=tool.description or "",
            input_schema=dict(tool.input_schema or {}),
            output_schema=dict(tool.output_schema) if tool.output_schema else None,
        )
        for tool in listed.tools
    ]


class McpSession:
    def __init__(
        self,
        settings: RunSettings,
        env: Mapping[str, str] | None = None,
        command: list[str] | None = None,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        server_env = settings.server_env(env)
        executable, *args = command or [settings.python, "-m", SERVER_MODULE]
        self._params = StdioServerParameters(command=executable, args=args, env=server_env, cwd=str(settings.work_dir))
        self._work_dir = settings.work_dir
        self._stderr_path = settings.work_dir / "server.stderr.log"
        self._grace_s = settings.mcp_grace_s
        self._tool_deadline_s = _tool_deadline(server_env)
        self._connect_timeout_s = connect_timeout_s
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._alive = False
        self.instructions: str | None = None
        self.tools: list[McpToolSpec] = []

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def stderr_path(self) -> Path:
        return self._stderr_path

    def stderr_tail(self) -> str:
        try:
            return self._stderr_path.read_text(encoding="utf-8", errors="replace")[-STDERR_TAIL_CHARS:].strip()
        except OSError:
            return ""

    def spec(self, name: str) -> McpToolSpec | None:
        return next((tool for tool in self.tools if tool.name == name), None)

    async def start(self) -> McpSession:
        if self._stack is not None:
            raise RuntimeError("The MCP session is already started.")
        self._work_dir.mkdir(parents=True, exist_ok=True)
        stack = AsyncExitStack()
        try:
            errlog = stack.enter_context(self._stderr_path.open("w", encoding="utf-8"))
            read, write = await stack.enter_async_context(stdio_client(self._params, errlog=errlog))
            session = await stack.enter_async_context(
                ClientSession(read, write, read_timeout_seconds=self._connect_timeout_s)
            )
            initialized = await session.initialize()
            tools = _specs(await session.list_tools())
        except BaseException as exc:
            try:
                await stack.aclose()
            except Exception:
                pass
            if not isinstance(exc, Exception):
                raise
            tail = self.stderr_tail()
            detail = f" Server output: {tail}" if tail else ""
            raise McpUnavailable(f"The Bodhan MCP server could not be started ({type(exc).__name__}).{detail}") from exc
        self._stack = stack
        self._session = session
        self._alive = True
        self.instructions = getattr(initialized, "instructions", None)
        self.tools = tools
        return self

    async def __aenter__(self) -> McpSession:
        return await self.start()

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _failure(self, name: str, started: float, **error: Any) -> McpResult:
        return McpResult(tool=name, ok=False, error=McpError(**error), latency_ms=(perf_counter() - started) * 1000)

    def _timeout(self, deadline: float | None) -> float:
        timeout = self._tool_deadline_s + self._grace_s
        if deadline is not None:
            timeout = min(timeout, deadline - monotonic())
        return timeout

    def _timed_out(self, name: str, started: float, timeout: float) -> McpResult:
        return self._failure(
            name, started, code="tool_timeout", retryable=True, uncertain=True,
            message=f"'{name}' gave no answer within {timeout:.0f} s; it may still have run and been billed.",
        )  # fmt: skip

    async def call(self, name: str, arguments: dict[str, Any], *, deadline: float | None = None) -> McpResult:
        started = perf_counter()
        if not self._alive or self._session is None:
            return self._failure(
                name, started, code="server_unavailable", message="The Bodhan MCP server is not running."
            )
        if self.spec(name) is None:
            known = ", ".join(tool.name for tool in self.tools)
            return self._failure(
                name, started, code="unknown_tool", message=f"Unknown tool '{name}'. Available: {known}."
            )
        timeout = self._timeout(deadline)
        if timeout <= 0:
            return self._failure(
                name, started, code="tool_timeout", message="No time is left in the run for this call."
            )
        try:
            result = await self._session.call_tool(name, arguments, read_timeout_seconds=timeout)
        except TimeoutError:
            return self._timed_out(name, started, timeout)
        except MCPError as exc:
            if exc.code == REQUEST_TIMEOUT:
                return self._timed_out(name, started, timeout)
            rejected = exc.code == INVALID_PARAMS
            return self._failure(name, started, code="protocol_error", message=str(exc), uncertain=not rejected)
        except _TRANSPORT_ERRORS as exc:
            self._alive = False
            tail = self.stderr_tail()
            return self._failure(
                name, started, code="server_unavailable", uncertain=True,
                message=f"The Bodhan MCP server stopped during '{name}' ({type(exc).__name__}).",
                details={"server_output": tail} if tail else {},
            )  # fmt: skip
        except Exception as exc:
            return self._failure(
                name, started, code="protocol_error", uncertain=True, message=f"{type(exc).__name__}: {exc}"
            )
        latency_ms = (perf_counter() - started) * 1000
        if getattr(result, "is_error", False):
            error = _server_error(_text(result), getattr(result, "structured_content", None))
            return McpResult(tool=name, ok=False, error=error, latency_ms=latency_ms)
        data = _success_data(result)
        if data is None:
            return McpResult(
                tool=name,
                ok=False,
                error=McpError(
                    code="protocol_error", message=f"'{name}' returned no structured result.", uncertain=True
                ),
                latency_ms=latency_ms,
            )
        return McpResult(tool=name, ok=True, data=data, latency_ms=latency_ms)

    async def aclose(self) -> None:
        stack, self._stack = self._stack, None
        self._alive = False
        self._session = None
        if stack is None:
            return
        try:
            await stack.aclose()
        except Exception:
            pass
