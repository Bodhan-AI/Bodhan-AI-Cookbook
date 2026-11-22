"""Run just the Marathi tool without MCP Inspector's 60-second request timeout.

Run from examples/bodhan_task_agent with your existing virtual environment.
Only uses Bodhan Indic-Translate through the existing ToolHub/Gateway.
Does not log credentials. An external API request can incur Bodhan charges.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bodhan_shared.config import load_dotenv
from bodhan_shared.tools import ToolHub


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="artifacts/worksheet_demo.ocr.md")
    parser.add_argument("--output", default="artifacts/worksheet_demo.translated.mr.md")
    parser.add_argument("--max-cost-inr", type=float, default=3)
    parser.add_argument("--force", action="store_true", help="Retry even when an output file already exists")
    args = parser.parse_args()
    load_dotenv()
    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"INPUT_MISSING: {input_path}. Use the OCR file already generated.")
        return 2
    if output_path.exists() and output_path.stat().st_size > 0 and not args.force:
        print(f"EXISTING_OUTPUT: {output_path}. Inspect it before requesting translation again.")
        return 0

    hub = ToolHub(Path.cwd(), maximum_cost_inr=args.max_cost_inr)
    result = hub.call("translate_text", {
        "target_language": "mr",
        "input_path": input_path.as_posix(),
        "output_path": output_path.as_posix(),
    })
    # Safe for Windows legacy code pages; JSON parser restores actual Unicode.
    print(json.dumps({
        "ok": result.get("ok", False),
        "error": result.get("error"),
        "output_path": result.get("output_path"),
        "qa": result.get("qa"),
        "estimated_bodhan_cost_inr": round(hub.budget.spent, 4),
    }, ensure_ascii=True, indent=2))
    if not result.get("ok") or not output_path.is_file():
        return 1
    print(f"OUTPUT_CREATED: {output_path}. Review language, table and formula manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
