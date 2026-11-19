#!/usr/bin/env python3
"""Unified, no-inference readiness verification for Bodhan MCP and agent components.

This script performs local imports/configuration checks and HTTP reachability probes
only.  It never invokes OCR, transcription, translation, speech synthesis, or an
LLM completion.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from pathlib import Path
from typing import Iterable

import httpx

_RECIPE_DIR = Path(__file__).resolve().parent
if str(_RECIPE_DIR) not in sys.path:
    sys.path.insert(0, str(_RECIPE_DIR))


def _normalize_mode(mode: str) -> str:
    mode = mode.strip().lower()
    if mode in {"combined", "both", "all"}:
        return "combined"
    if mode in {"mcp", "mcp-only"}:
        return "mcp"
    if mode in {"agent", "agent-only"}:
        return "agent"
    raise argparse.ArgumentTypeError("mode must be mcp, agent, or combined")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Bodhan MCP/agent readiness without making inference calls.",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        type=_normalize_mode,
        default="combined",
        help="Check mode: mcp, agent, or combined (default)",
    )
    parser.add_argument("--mode", dest="mode_option", choices=("mcp", "agent", "combined", "both", "all"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--check", dest="check_option", choices=("mcp", "agent", "combined", "both", "all"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--mcp-only", action="store_true", help="Run only the local MCP protocol check")
    parser.add_argument("--agent-only", action="store_true", help="Run only agent local/config/reachability checks")
    parser.add_argument("--combined", action="store_true", help="Run MCP and agent checks (default)")
    parser.add_argument("--files", "--input", dest="input_path", default=None,
                        help="Optional input file or directory to validate locally")
    parser.add_argument("--no-network", action="store_true",
                        help="Skip network reachability probes")
    parser.add_argument("--timeout", type=float, default=5.0,
                        help="Network probe timeout in seconds")
    return parser


def _configured(value: str) -> bool:
    if not value or not value.strip():
        return False
    lowered = value.strip().lower()
    return not (
        lowered.startswith("your_")
        or lowered.startswith("replace")
        or lowered in {"changeme", "todo", "none", "null"}
    )


def _print_check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")


async def probe_reachability(
    base_url: str,
    *,
    timeout: float = 5.0,
) -> dict[str, object]:
    """Probe GET /models without credentials or inference."""
    if not base_url:
        return {"reachable": False, "detail": "base URL is not configured"}

    url = base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
    except httpx.TimeoutException:
        return {"reachable": False, "detail": "timeout"}
    except httpx.HTTPError as exc:
        return {
            "reachable": False,
            "detail": f"network error ({type(exc).__name__})",
        }

    status = response.status_code
    detail = f"/models responded HTTP {status}; no inference or credential validation"
    return {"reachable": status < 500, "detail": detail}


async def verify_mcp() -> int:
    """Reuse verify_mcp.py's in-process MCP client/server test."""
    print("\nMCP readiness")
    print("-" * 30)
    try:
        verify_module = importlib.import_module("verify_mcp")
        verify_fn = getattr(verify_module, "verify_mcp_client")
        return await verify_fn()
    except Exception as exc:
        _print_check("MCP harness", False, f"could not run: {type(exc).__name__}")
        return 1


async def verify_agent(no_network: bool = False, timeout: float = 5.0, input_path: str | None = None) -> int:
    """Check agent imports, config, input path, endpoint reachability, and auth."""
    print("\nAgent readiness")
    print("-" * 30)
    failures = 0

    try:
        agent_module = importlib.import_module("src.bodhan_agent.agent")
        config_module = importlib.import_module("src.common.config")
        _print_check("Agent module import", True)
    except Exception as exc:
        _print_check("Agent module import", False, f"import error: {type(exc).__name__}")
        return 1

    try:
        cfg = config_module.load_config(load_env_file=True)
        _print_check("Configuration load", True)
    except Exception as exc:
        _print_check("Configuration load", False, f"config error: {type(exc).__name__}")
        return 1

    bodhan_keys = {
        "transcribe": cfg.transcribe_key,
        "translate": cfg.translate_key,
        "speak": cfg.speak_key,
        "OCR": cfg.ocr_key,
    }
    for modality, key in bodhan_keys.items():
        configured = _configured(key)
        _print_check(
            f"Bodhan {modality} credential",
            configured,
            "configured (value hidden)" if configured else "not configured",
        )
        if not configured:
            failures += 1

    llm_key_configured = _configured(cfg.llm_api_key)
    _print_check(
        "LLM credentials",
        llm_key_configured,
        "configured" if llm_key_configured else "no usable key configured",
    )
    if not llm_key_configured:
        failures += 1
    if cfg.llm_model:
        print(f"  [PASS] LLM model setting - name configured (value not displayed)")
    else:
        _print_check("LLM model setting", False, "model name is empty")
        failures += 1

    if input_path:
        path = Path(input_path).expanduser()
        valid_input = (
            path.is_file() and path.stat().st_size > 0
        ) or (
            path.is_dir() and any(item.is_file() and item.stat().st_size > 0 for item in path.iterdir())
        )
        _print_check("Agent input", valid_input, str(path) if valid_input else f"missing or empty: {path}")
        if not valid_input:
            failures += 1
    else:
        print("  [INFO] Agent input - no input path supplied; local path check skipped")

    if no_network:
        print("  [INFO] Network probes skipped (--no-network)")
    else:
        bodhan_probe = await probe_reachability(cfg.base_url, timeout=timeout)
        bodhan_reachable = bool(bodhan_probe["reachable"])
        _print_check("Bodhan endpoint reachability", bodhan_reachable, str(bodhan_probe["detail"]))
        if not bodhan_reachable:
            failures += 1

        llm_base_url = cfg.llm_base_url or "https://api.openai.com/v1"
        llm_probe = await probe_reachability(
            llm_base_url,
            timeout=timeout,
        )
        llm_reachable = bool(llm_probe["reachable"])
        _print_check("LLM endpoint reachability", llm_reachable, str(llm_probe["detail"]))
        if not llm_reachable:
            failures += 1

    return 1 if failures else 0


async def verify_combined(no_network: bool = False, timeout: float = 5.0, input_path: str | None = None) -> int:
    mcp_code = await verify_mcp()
    agent_code = await verify_agent(no_network=no_network, timeout=timeout, input_path=input_path)
    print("\nOverall readiness")
    print("-" * 30)
    if mcp_code == 0 and agent_code == 0:
        print("  [PASS] Combined readiness - MCP and agent checks passed")
        return 0
    print("  [FAIL] Combined readiness - at least one check failed")
    return 1


def _resolve_mode(args: argparse.Namespace) -> str:
    if args.mcp_only:
        return "mcp"
    if args.agent_only:
        return "agent"
    if args.combined:
        return "combined"
    if args.mode_option:
        return _normalize_mode(args.mode_option)
    if args.check_option:
        return _normalize_mode(args.check_option)
    return _normalize_mode(args.mode)


async def run(args: argparse.Namespace) -> int:
    mode = _resolve_mode(args)
    print("=" * 70)
    print(" Bodhan Unified Working / Readiness Check")
    print("=" * 70)
    print("No inference calls: MCP uses local protocol checks; agent checks are local + reachability probes.")
    if mode == "mcp":
        return await verify_mcp()
    if mode == "agent":
        return await verify_agent(
            no_network=args.no_network,
            timeout=args.timeout,
            input_path=args.input_path,
        )
    return await verify_combined(
        no_network=args.no_network,
        timeout=args.timeout,
        input_path=args.input_path,
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
