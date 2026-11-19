# Task result

**Task:** Translate this worksheet into Marathi and keep the table

**Status:** ✅ success

## Answer

The worksheet image has been OCR‑processed and translated into Marathi, preserving the original table structure.

## Artifacts

- [worksheet_translated.md](artifacts\worksheet_translated.md) — Markdown file containing the Marathi translation of the worksheet with table retained (verified)

## Assumptions

_None._

## Verification

- **worksheet_translated.md**: pass — check=coverage + structure + target script, coverage_ok=True, structure_preserved=True, target_script_share=1.0, units_translated=20, units_total=20

## What was done

| # | Tool | Why | Result | Cost (₹) | Latency |
|---|---|---|---|---|---|
| pre | `inspect_files` | pre-flight: learn file types, durations, pages and scripts (free) | ok | 0.0000 | 1.0s |
| 1 | `ocr_document` | OCR the worksheet image to get text and tables. | ok | 0.0000 | 1.3s |
| 2 | `translate_text` | Translate OCR markdown to Marathi. | ok | 0.0000 | 0.1s |

## Notes

- Rough cost to process every input once with its main model: Rs 0.20 (budget Rs 20.00).

## Cost and time

- Bodhan cost: **₹0.0000** of ₹20.0 budget
- By model: {"indic-ocr": {"calls": 0, "cached": 1, "cost_inr": 0.0}, "indic-translate": {"calls": 0, "cached": 19, "cost_inr": 0.0}}
- Tool calls: 2/30; LLM calls: 4 ({'prompt_tokens': 13422, 'completion_tokens': 961} tokens, model openai/gpt-oss-20b)
- Wall time: 56.94s

Full step-by-step record: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).
