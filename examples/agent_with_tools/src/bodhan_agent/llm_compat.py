"""Compatibility shim for OpenAI-compatible providers that attach extra data to tool calls.

Gemini 3 models return `extra_content.google.thought_signature` on every tool call and answer HTTP 400 if the next
request does not send it back unchanged. The LangChain OpenAI client drops unknown fields, so this transport
remembers `extra_content` per tool-call id when a response arrives and puts it back into the assistant messages of
later requests. For providers that send no `extra_content` it does nothing.
"""

from __future__ import annotations

import json

import httpx


class ToolCallExtrasTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.extras: dict[str, dict] = {}  # tool_call id -> extra_content

    def _restore(self, body: dict) -> bool:
        changed = False
        for msg in body.get("messages", []):
            for call in msg.get("tool_calls") or []:
                extra = self.extras.get(call.get("id"))
                if extra and "extra_content" not in call:
                    call["extra_content"] = extra
                    changed = True
        return changed

    def _remember(self, body: dict) -> None:
        for choice in body.get("choices", []):
            for call in (choice.get("message") or {}).get("tool_calls") or []:
                if call.get("extra_content") and call.get("id"):
                    self.extras[call["id"]] = call["extra_content"]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.extras and request.content:
            try:
                body = json.loads(request.content)
                if self._restore(body):
                    data = json.dumps(body).encode("utf-8")
                    headers = {k: v for k, v in request.headers.items() if k.lower() != "content-length"}
                    request = httpx.Request(request.method, request.url, headers=headers, content=data, extensions=request.extensions)
            except (ValueError, AttributeError):
                pass  # not a JSON chat request: send it unchanged
        response = await self.inner.handle_async_request(request)
        if response.status_code == 200 and "json" in response.headers.get("content-type", ""):
            await response.aread()
            try:
                self._remember(json.loads(response.content))
            except (ValueError, AttributeError):
                pass
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


def async_client(timeout: float = 120.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ToolCallExtrasTransport(), timeout=timeout)
