#!/usr/bin/env python3
"""Generates three short audio clips (Telugu, Hindi, Tamil) for Task 4.

Uses IndicTranslateTool and IndicSpeakTool directly (plain code, no LLM).
All calls log to the central spend ledger under run_id='prep_clips'.
Skips all paid calls when the output files already exist.
"""

import asyncio
import sys
from pathlib import Path
import dotenv

from bodhan_mcp.tools.translate import IndicTranslateTool
from bodhan_mcp.tools.speak import IndicSpeakTool

# Load environment
dotenv.load_dotenv(override=True)


CLIPS_DATA = [
    {
        "lang": "te",
        "lang_name": "Telugu",
        "english": "The library is closed on Sundays.",
        "out_file": "samples/clip_telugu.wav",
        "voice": "Sravani",
    },
    {
        "lang": "hi",
        "lang_name": "Hindi",
        "english": "The train arrives at six in the evening.",
        "out_file": "samples/clip_hindi.wav",
        "voice": "Kavya",
    },
    {
        "lang": "ta",
        "lang_name": "Tamil",
        "english": "Please submit the report by Friday.",
        "out_file": "samples/clip_tamil.wav",
        "voice": "Anitha",
    },
]


async def main():
    print("=== Generating Task 4 Audio Clips ===")
    run_id = "prep_clips"
    Path("samples").mkdir(parents=True, exist_ok=True)

    translate_tool = None
    speak_tool = None

    for item in CLIPS_DATA:
        lang_code = item["lang"]
        lang_name = item["lang_name"]
        en_text = item["english"]
        out_path = Path(item["out_file"])
        voice = item["voice"]

        if out_path.exists() and out_path.stat().st_size > 0:
            print(f"{out_path} already exists, skipping")
            continue

        if translate_tool is None:
            translate_tool = IndicTranslateTool(run_id=run_id)
            speak_tool = IndicSpeakTool(run_id=run_id)

        print(f"\nProcessing {lang_name} ({lang_code})...")
        print(f"  English: '{en_text}'")

        # 1. Translate
        t_res = await translate_tool.translate(
            text=en_text,
            target_language=lang_code,
            source_language="en",
        )
        if not t_res.success or not t_res.translated_text:
            err = t_res.error
            print(
                f"  Translate failed: {err.code if err else 'UNKNOWN'} - {err.message if err else ''}"
            )
            sys.exit(1)

        translated = t_res.translated_text
        print(f"  Translated ({lang_name}): '{translated}'")

        # 2. Speak
        s_res = await speak_tool.speak(
            input_text=translated,
            language=lang_code,
            voice=voice,
            output_path=out_path,
        )
        if not s_res.success:
            err = s_res.error
            print(
                f"  Speak failed: {err.code if err else 'UNKNOWN'} - {err.message if err else ''}"
            )
            sys.exit(1)

        print(
            f"  Created audio: {out_path} ({s_res.duration_seconds:.2f}s, Rs {s_res.estimated_cost_inr:.4f})"
        )

    print("\nTask 4 audio clips ready.")


if __name__ == "__main__":
    asyncio.run(main())
