# Task Result (SUCCESS)

**Task:** Translate this worksheet into Marathi and keep the table intact in Markdown
**Status:** `success`
**Total Bodhan Cost:** ₹0.0000 / ₹5.00
**Steps Used:** 5 / 20 | **Runtime:** 11.11s | **Cache Hits:** 2

## Answer & Summary

The worksheet image was OCR'd into Markdown, translated into Marathi (native Devanagari script) with its table structure kept as a Markdown table, and published as worksheet_mr.md.

## Artifacts Produced

- **d1** (`markdown`): [artifacts/worksheet_mr.md](artifacts/worksheet_mr.md)

## Assumptions

- The worksheet is a single-page image; the entire page is the source text to OCR and translate.
- The source language is not stated and the script histogram is empty because the image has not been read, so the source language is left as unknown and should be determined from the OCR output.
- Table structure is preserved by mapping rows and columns to a Markdown table with the same cell layout; translated cell text stays in the same cell positions.
- Marathi output uses native Devanagari script.
- Estimated cost is dominated by one indic-ocr page call (₹0.20) plus indic-translate output tokens, well within the ₹5.00 budget.
- The worksheet is a single-page PNG, so ocr_document needs no page selection.
- The source language is unknown and is left for OCR and translation to handle, since no source language is required as an argument.
- The translator keeps Markdown table syntax intact when given Markdown input; table cell positions are preserved.
- No speech synthesis is requested, so no verification step is needed.
- Estimated cost is one OCR page (about ₹0.20) plus translation output, well within the ₹5.00 budget.

## Verification Summary

| Deliverable | Kind | Passed | Checks |
| --- | --- | --- | --- |
| `d1` | `markdown` | PASS | PASS file_non_empty (worksheet_mr.md (1004 bytes)); PASS indic_provenance (All Indic text verified from Bodhan/input); PASS target_script_ratio (script_ratio=1.00 for 'mr' (min 0.80)); PASS translation_chunk_coverage (chunks_in=1, chunks_out=1); PASS tables_intact (source_tables=[{'rows': 5, 'cols': 4}], output_tables=[{'rows': 5, 'cols': 4}]); PASS digits_preserved (preserved=12/12 (100%), missing=[]) |

## Trace & Report

- Structured report: [report.json](report.json)
- Step-by-step trace: [trace.jsonl](trace.jsonl)
- Visual trace viewer: [trace.html](trace.html)
