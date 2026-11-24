#!/usr/bin/env bash
set -euo pipefail

# Run all 6 demo tasks + the bonus CaMeL prompt-injection demo end to end.
# Prerequisites:
#   1. .env populated with Bodhan keys and LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
#   2. Fixtures generated: python scripts/make_fixtures.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECIPE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$RECIPE_DIR"

PYTHON="${PYTHON:-.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

export BODHAN_DEV_LEDGER_PATH="${BODHAN_DEV_LEDGER_PATH:-outputs/dev_spend_ledger.json}"
export BODHAN_DEV_SPEND_CAP_INR="${BODHAN_DEV_SPEND_CAP_INR:-50}"

echo "=== 0. Ensuring fixtures exist in sample_data/ ==="
if [[ ! -f sample_data/chapter_8p.pdf || ! -f sample_data/hindi_voice_note_3min.wav ]]; then
  "$PYTHON" scripts/make_fixtures.py
fi

echo ""
echo "=== Task 1: Two-minute Tamil audio summary of 8-page scanned PDF ==="
"$PYTHON" -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter, and also save the Tamil summary text" \
  --files sample_data/chapter_8p.pdf \
  --out outputs/demo_01_tamil_summary \
  --max-steps 30 --max-cost-inr 20

echo ""
echo "=== Task 2: Transcribe Hindi voice note and generate English SRT subtitles ==="
"$PYTHON" -m bodhan_agent \
  --task "Transcribe this Hindi voice note and give me English subtitles in SRT format" \
  --files sample_data/hindi_voice_note_3min.wav \
  --out outputs/demo_02_hindi_srt \
  --max-steps 30 --max-cost-inr 10

echo ""
echo "=== Task 3: Translate worksheet photo into Marathi Markdown keeping the table intact ==="
"$PYTHON" -m bodhan_agent \
  --task "Translate this worksheet into Marathi and keep the table intact in Markdown" \
  --files sample_data/worksheet.png \
  --out outputs/demo_03_marathi_worksheet \
  --max-steps 20 --max-cost-inr 5

echo ""
echo "=== Task 4: Five clips -> identify Telugu clips and translate those into English ==="
"$PYTHON" -m bodhan_agent \
  --task "Which of these clips are in Telugu? Translate those into English" \
  --files sample_data/clips/ \
  --out outputs/demo_04_telugu_clips \
  --max-steps 30 --max-cost-inr 10

echo ""
echo "=== Task 5: Read Bengali paragraph aloud with a female voice ==="
"$PYTHON" -m bodhan_agent \
  --task "Read this paragraph aloud in Bengali with a female voice" \
  --files sample_data/bengali_paragraph.txt \
  --out outputs/demo_05_bengali_tts \
  --max-steps 15 --max-cost-inr 5

echo ""
echo "=== Task 6: Impossible task — Translate this video into French (₹0 rejection) ==="
"$PYTHON" -m bodhan_agent \
  --task "Translate this video into French" \
  --files sample_data/sample_video.mp4 \
  --out outputs/demo_06_impossible_french \
  --max-steps 10 --max-cost-inr 5

echo ""
echo "=== Task 7 (Bonus): CaMeL prompt-injection defense on scanned worksheet ==="
"$PYTHON" -m bodhan_agent \
  --task "Translate this worksheet into Marathi and keep the table intact" \
  --files sample_data/injection_page.png \
  --out outputs/demo_07_camel_injection \
  --max-steps 20 --max-cost-inr 5

echo ""
echo "=== Demo Summary ==="
"$PYTHON" -c "
import json, pathlib
rows = []
for d in sorted(pathlib.Path('outputs').glob('demo_*')):
    rp = d / 'report.json'
    if rp.exists():
        r = json.loads(rp.read_text())
        print(f\"{d.name:28} status={r['status']:10} cost=₹{r['total_cost_inr']:.4f} steps={r['steps_used']:2} time={r['total_time_s']:.1f}s\")
"
echo ""
echo "Open visual trace viewer in your browser: outputs/demo_01_tamil_summary/trace.html"
