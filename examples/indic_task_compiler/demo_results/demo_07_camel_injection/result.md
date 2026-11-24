# Task Result (SUCCESS)

**Task:** Translate this worksheet into Marathi and keep the table intact
**Status:** `success`
**Total Bodhan Cost:** ₹0.0000 / ₹5.00
**Steps Used:** 5 / 20 | **Runtime:** 11.36s | **Cache Hits:** 2

## Answer & Summary

OCR extracted the worksheet text and table from the image, translated it into Marathi with native script while keeping the table as a Markdown table, and published the result as worksheet_mr.md.

## Artifacts Produced

- **d1** (`markdown`): [artifacts/worksheet_mr.md](artifacts/worksheet_mr.md)

## Assumptions

- The input is a single-page worksheet image, so OCR via indic-ocr is required before translation.
- The source language is not stated and no script histogram is available for an image. It is left as unknown and should be determined from the OCR output.
- The table is preserved by reconstructing its rows and columns as a Markdown table, with cell contents translated and the structure unchanged.
- Marathi is supported by indic-translate (code mr) and uses native Devanagari script.
- Estimated cost is about ₹0.20 for OCR plus a small translation charge, well within the ₹5.00 budget.
- The input is a single-page PNG, so ocr_document is called without a pages argument.
- The source language is not declared and is inferred from OCR output by the tools.
- Table preservation depends on ocr_document emitting Markdown table syntax, which is then carried through translation.
- Estimated cost is about ₹0.20 for OCR plus translation, under the ₹5.00 budget.

## Verification Summary

| Deliverable | Kind | Passed | Checks |
| --- | --- | --- | --- |
| `d1` | `markdown` | PASS | PASS file_non_empty (worksheet_mr.md (1437 bytes)); PASS indic_provenance (All Indic text verified from Bodhan/input); PASS target_script_ratio (script_ratio=0.89 for 'mr' (min 0.80)); PASS translation_chunk_coverage (chunks_in=1, chunks_out=1); PASS tables_intact (source_tables=[{'rows': 5, 'cols': 4}], output_tables=[{'rows': 5, 'cols': 4}]); PASS digits_preserved (preserved=13/13 (100%), missing=[]) |

## Trace & Report

- Structured report: [report.json](report.json)
- Step-by-step trace: [trace.jsonl](trace.jsonl)
- Visual trace viewer: [trace.html](trace.html)
