"""Global pytest fixtures and isolation guards."""

import math
import struct
import sys
import wave
from pathlib import Path
from typing import Any

import pytest

# Windows sandbox isolation guard: patch Proactor self-pipe because loopback TCP is blackholed in sandbox
if sys.platform == "win32":
    try:
        import asyncio.proactor_events

        class _DummySock:
            def fileno(self):
                return -1

            def setblocking(self, b):
                pass

            def close(self):
                pass

        def _dummy_make_self_pipe(self):
            self._ssock = _DummySock()
            self._csock = _DummySock()
            self._internal_fds = 0

        asyncio.proactor_events.BaseProactorEventLoop._make_self_pipe = _dummy_make_self_pipe
        asyncio.proactor_events.BaseProactorEventLoop._loop_self_reading = lambda self, f=None: None
        asyncio.proactor_events.BaseProactorEventLoop._write_to_self = lambda self: None
    except Exception:
        pass


@pytest.fixture(autouse=True)
def block_real_http(monkeypatch):
    """Ensure pytest NEVER makes unmocked HTTP requests to Bodhan API (Standing Rule F2)."""
    import httpx

    original_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send

    def forbidden_send(self, request, *args, **kwargs):
        host = getattr(request.url, "host", "") or ""
        transport = getattr(self, "_transport", None)
        if ("bodhan.ai" in host or "api." in host) and not isinstance(transport, httpx.MockTransport):
            raise RuntimeError(
                f"Real HTTP call to {request.url} attempted in pytest! "
                "All Bodhan HTTP calls must be mocked per AGENTS.md and Standing Rule F2."
            )
        return original_send(self, request, *args, **kwargs)

    async def forbidden_async_send(self, request, *args, **kwargs):
        host = getattr(request.url, "host", "") or ""
        transport = getattr(self, "_transport", None)
        if ("bodhan.ai" in host or "api." in host) and not isinstance(transport, httpx.MockTransport):
            raise RuntimeError(
                f"Real HTTP call to {request.url} attempted in pytest! "
                "All Bodhan HTTP calls must be mocked per AGENTS.md and Standing Rule F2."
            )
        return await original_async_send(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", forbidden_send)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_async_send)


@pytest.fixture
def short_wav(tmp_path: Path) -> Path:
    """Generate a minimal valid 1-second 16kHz mono 16-bit PCM WAV file."""
    wav_path = tmp_path / "sample.wav"
    sample_rate = 16000
    duration_s = 1.0
    num_samples = int(sample_rate * duration_s)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(num_samples):
            # 440 Hz tone
            val = int(32767.0 * 0.5 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate))
            wf.writeframesraw(struct.pack("<h", val))
    return wav_path


@pytest.fixture
def small_pdf(tmp_path: Path) -> Path:
    """Generate a minimal valid 1-page PDF file."""
    pdf_path = tmp_path / "sample.pdf"
    # Minimal standard PDF 1.4 byte payload with sample text
    raw_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 44 >> stream\n"
        b"BT /F1 12 Tf 72 712 Td (Hello Bodhan) Tj ET\n"
        b"endstream\n"
        b"endobj\n"
        b"xref\n"
        b"0 5\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000214 00000 n \n"
        b"trailer << /Size 5 /Root 1 0 R >>\n"
        b"startxref\n"
        b"309\n"
        b"%%EOF\n"
    )
    pdf_path.write_bytes(raw_pdf)
    return pdf_path


@pytest.fixture
def sample_image(tmp_path: Path) -> Path:
    """Generate a minimal valid 1x1 PNG image."""
    png_path = tmp_path / "sample.png"
    # Minimal valid 1x1 red PNG hex representation
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
        "0000000c4944415408d763f8cfc000000301010018dd8d100000000049454e44ae"
        "426082"
    )
    png_path.write_bytes(png_bytes)
    return png_path


class FakeMCPTransport:
    """Fake MCP transport for unit-testing agent interactions without subprocess."""

    def __init__(self):
        self.tool_calls: list[dict[str, Any]] = []
        self.responses: dict[str, Any] = {}

    def register_response(self, tool_name: str, response: Any) -> None:
        self.responses[tool_name] = response

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.tool_calls.append({"name": name, "arguments": arguments})
        if name in self.responses:
            return self.responses[name]
        return {"status": "ok", "mocked": True, "tool": name}


@pytest.fixture
def fake_mcp_transport() -> FakeMCPTransport:
    return FakeMCPTransport()


class FakeLLM:
    """Fake LLM client providing deterministic responses for planner/resolver tests."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def complete(self, prompt: str, **kwargs: Any) -> str:
        self.calls.append({"prompt": prompt, "kwargs": kwargs})
        if self.responses:
            return self.responses.pop(0)
        return '{"steps": []}'

    async def acomplete(self, prompt: str, **kwargs: Any) -> str:
        return self.complete(prompt, **kwargs)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()
