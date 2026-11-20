"""Regenerate experiments/data/tool_catalog_snapshot.json.

Must run outside Jupyter: touching FastMCP's ``Tool.parameters`` deadlocks some
Jupyter kernels on Windows. Usage, from the recipe root:

    .venv\\Scripts\\python.exe experiments\\scripts\\refresh_catalog_snapshot.py

The snapshot records exactly what the LLM is offered, which is the model-facing
allowlist plus the two hand-written control tools -- not the full MCP registry.
Operator-only tools such as get_cache_stats/clear_cache stay registered on the
server but are deliberately absent here.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RECIPE_ROOT = Path(__file__).resolve().parents[2]
if str(RECIPE_ROOT) not in sys.path:
    sys.path.insert(0, str(RECIPE_ROOT))

from src.bodhan_agent.agent import (  # noqa: E402
    FINISH_TOOL,
    MODEL_FACING_TOOLS,
    SUBMIT_PLAN_TOOL,
    _get_openai_tool_definitions,
)
from src.bodhan_mcp.server import SUPPORTED_INDIC_LANGS, mcp  # noqa: E402


def _entry(func: dict, source: str) -> dict:
    return {
        "name": func["name"],
        "description": func["description"],
        "parameters": func["parameters"],
        "source": source,
    }


def build_snapshot() -> dict:
    mcp_tools = [
        _entry(tool["function"], "mcp")
        for tool in _get_openai_tool_definitions()
    ]
    control_tools = [
        _entry(tool["function"], "control")
        for tool in (SUBMIT_PLAN_TOOL, FINISH_TOOL)
    ]

    advertised = {tool["name"] for tool in mcp_tools}
    if advertised != set(MODEL_FACING_TOOLS):
        raise SystemExit(
            "Snapshot does not match MODEL_FACING_TOOLS: "
            f"missing={set(MODEL_FACING_TOOLS) - advertised}, "
            f"extra={advertised - set(MODEL_FACING_TOOLS)}"
        )

    registered = {tool.name for tool in mcp._tool_manager.list_tools()}
    operator_only = registered - advertised
    if not operator_only <= {"get_cache_stats", "clear_cache"}:
        raise SystemExit(
            f"Unexpected registered-but-unadvertised tools: {operator_only}"
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_facing_tools": sorted(advertised),
        "operator_only_tools": sorted(operator_only),
        "mcp_tools": mcp_tools,
        "control_tools": control_tools,
        "supported_langs": sorted(SUPPORTED_INDIC_LANGS),
    }


def main() -> int:
    snapshot = build_snapshot()
    out_path = RECIPE_ROOT / "experiments" / "data" / "tool_catalog_snapshot.json"
    out_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out_path}")
    print(f"  model-facing MCP tools: {len(snapshot['mcp_tools'])}")
    print(f"  operator-only: {snapshot['operator_only_tools']}")
    print(f"  control tools: {len(snapshot['control_tools'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
