"""Focused tests for the unified no-inference readiness checker."""

from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import verify_working


def test_default_parser_mode_is_combined():
    args = verify_working.build_parser().parse_args([])
    assert verify_working._resolve_mode(args) == "combined"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["mcp"], "mcp"),
        (["agent"], "agent"),
        (["combined"], "combined"),
        (["--mcp-only"], "mcp"),
        (["--agent-only"], "agent"),
    ],
)
def test_parser_modes(argv, expected):
    args = verify_working.build_parser().parse_args(argv)
    assert verify_working._resolve_mode(args) == expected


def test_probe_worker_treats_http_auth_response_as_reachable_without_credentials():
    response = SimpleNamespace(status_code=401)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            assert "Authorization" not in headers
            return response

    receiver, sender = multiprocessing.Pipe(duplex=False)
    try:
        with patch("verify_working.httpx.Client", FakeClient):
            verify_working._probe_worker("https://example.invalid/v1", 1.0, sender)
        result = receiver.recv()
    finally:
        receiver.close()

    assert result["reachable"] is True
    assert "HTTP 401" in str(result["detail"])
    assert "no inference or credential validation" in str(result["detail"])


def test_probe_terminates_worker_when_dns_or_network_stalls():
    class FakeConnection:
        def poll(self, timeout):
            return False

        def close(self):
            pass

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.running = False
            self.terminated = False
            self.exitcode = None

        def start(self):
            self.running = True

        def is_alive(self):
            return self.running

        def terminate(self):
            self.terminated = True
            self.running = False

        def join(self, timeout=None):
            pass

        def close(self):
            pass

    class FakeContext:
        def __init__(self):
            self.process = FakeProcess()

        def Pipe(self, duplex=False):
            return FakeConnection(), FakeConnection()

        def Process(self, **kwargs):
            return self.process

    context = FakeContext()
    with patch("verify_working.multiprocessing.get_context", return_value=context):
        result = asyncio.run(
            verify_working.probe_reachability(
                "https://example.invalid/v1",
                timeout=0.01,
            )
        )

    assert result == {"reachable": False, "detail": "timeout after 0.01s"}
    assert context.process.terminated is True


def test_agent_mode_uses_default_openai_endpoint_and_hides_secrets(tmp_path: Path, capsys):
    secrets = {
        "transcribe_key": "transcribe-secret",
        "translate_key": "translate-secret",
        "speak_key": "speak-secret",
        "ocr_key": "ocr-secret",
        "llm_api_key": "llm-secret",
    }
    cfg = SimpleNamespace(
        **secrets,
        base_url="https://api.bodhan.ai/v1",
        llm_base_url="",
        llm_model="gpt-4o-mini",
    )
    config_module = SimpleNamespace(load_config=lambda load_env_file=True: cfg)
    input_file = tmp_path / "input.pdf"
    input_file.write_bytes(b"pdf")

    async def probe(base_url: str, **kwargs):
        return {
            "reachable": True,
            "detail": "reachable only",
        }

    with (
        patch.object(
            verify_working,
            "probe_reachability",
            new=AsyncMock(side_effect=probe),
        ) as probe_mock,
        patch(
            "verify_working.importlib.import_module",
            side_effect=lambda name: config_module if name == "src.common.config" else SimpleNamespace(),
        ),
    ):
        result = asyncio.run(
            verify_working.verify_agent(input_path=str(input_file))
        )

    output = capsys.readouterr().out
    assert result == 0
    assert probe_mock.await_args_list[1].args[0] == "https://api.openai.com/v1"
    for secret in secrets.values():
        assert secret not in output


def test_verify_mcp_reuses_existing_harness():
    async def run():
        fake_module = SimpleNamespace(verify_mcp_client=AsyncMock(return_value=0))
        with patch("verify_working.importlib.import_module", return_value=fake_module):
            return await verify_working.verify_mcp()

    assert asyncio.run(run()) == 0
