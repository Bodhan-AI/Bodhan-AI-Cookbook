#!/usr/bin/env python3
"""Generates samples/bengali_notice.txt using IndicTranslateTool (plain code, no LLM).

All calls log to the central spend ledger under run_id='prep_bengali'.
Skips all paid calls when the output file already exists.
"""

import asyncio
import sys
from pathlib import Path
import dotenv

from bodhan_mcp.tools.translate import IndicTranslateTool

dotenv.load_dotenv(override=True)


async def main():
    out_path = Path("samples/bengali_notice.txt")
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"{out_path} already exists, skipping")
        return

    en_notice = "All students must submit their assignments before five in the afternoon."
    print(f"Translating English notice to Bengali: '{en_notice}'")

    tool = IndicTranslateTool(run_id="prep_bengali")
    res = await tool.translate(text=en_notice, target_language="bn", source_language="en")
    if not res.success or not res.translated_text:
        err = res.error
        print(
            f"Translation failed: {err.code if err else 'UNKNOWN'} - {err.message if err else ''}"
        )
        sys.exit(1)

    out_path.write_text(res.translated_text, encoding="utf-8")
    print(f"Created {out_path}: '{res.translated_text}' (billed Rs {res.estimated_cost_inr:.4f})")


if __name__ == "__main__":
    asyncio.run(main())
