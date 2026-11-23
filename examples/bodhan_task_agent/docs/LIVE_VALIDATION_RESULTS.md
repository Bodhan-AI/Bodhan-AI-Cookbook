# Live validation evidence — 23 November 2026

## Scope and interpretation

These six scenarios are local live demonstrations, **not** Bodhan's hidden-test evaluation. Successful `report.json` status is necessary but not sufficient: real artifacts, structural/semantic checks, failure traces and limitations are reported separately. Every estimate below is a Bodhan-only cost estimate, excluding the planning LLM.

| # | Task | Report status | What actually worked | Remaining limitations | Steps | Seconds | INR estimated |
| --- | --- | --- | --- | --- | ---: | ---: | ---: |
| 1 | Scanned chapter → Tamil summary/narration | `completed` | MP3 and Tamil summary generated; Bodhan ASR verified; 5 pages OCR'd | **Partial:** 3/8 pages failed OCR HTTP 502 (1,2,8); 2m42s not 2m; ASR similarity 0.714; translation roundtrip 0.365; multi-step run >5m | 17 | 442.02 | 3.6402 |
| 2 | Hindi voice note → English SRT | `completed` | One 6.485s subtitle cue from Bodhan ASR/translation | Very short input, not three-minute task; word-level timing unavailable; recovered two tool errors, redundant request | 19 | 121.67 | 0.0266 |
| 3 | Worksheet photograph → Marathi table | `completed` | Agent called Bodhan OCR and Indic-Translate, saved Markdown/table/QA files | `numbers_preserved: false` strict QA; number values and table present but `9:00 AM` awkwardly rendered `9:00ए. एम.`; extra translation attempt not integrated; native-speaker review pending | 11 | 74.40 | 0.2953 |
| 4 | Five audio clips → identify Telugu | `completed` | Correct Telugu clip_01 and clip_04 chosen and translated; clips 02 and 05 skipped | English clip_03 had no provider language code and was labeled unknown; one recovered `PATH_OUTSIDE_WORKSPACE` error | 21 | 88.34 | 0.0411 |
| 5 | Bengali female narration | `completed` | 12.032s WAV using fixed voice Ishita; back-ASR similarity 0.975 | Similarity diagnostic, not native-speaker QA | 7 | 19.39 | 0.1071 |
| 6 | MP4 → unsupported French video | `not_completed` (**expected**) | Generic language-registry preflight returns `UNSUPPORTED_LANGUAGE`, no model or tool calls, no fake artifact | Early refusal does not validate video media processing; explicit-target heuristic is conservative | 0 | 0.00 (reported) | 0.0000 |

## External MCP client

MCP Inspector discovered nine tools using `tools/list` and successfully invoked `inspect_files`, `read_text` and `ocr_document` on local test data. The OCR output included the table and math expression. A low-confidence review flag was retained for empty header/footer layout blocks. This does not establish that every MCP tool has been independently tested.

## Tests and checks

The 37-test original recipe suite passed in the Windows submission clone. The newer supported-language preflight introduces 11 parameterized/integration regression cases: **re-run the full suite and report the actual count from the final PR checkout**. Cookbook PR-specific validation passed and the repository-wide secret scan passed. Native-Windows repository-wide pytest was 30 passed/2 failed due to pre-existing AndroidSpeak validation issues and a Unix `cp` subprocess in upstream tests, respectively; these are distinct from recipe test results. The two Groq URL warnings in test fixtures are non-blocking companion-LLM references, not non-Bodhan language processing.

## Public evidence hosting — not yet completed

Raw output reports and traces exist locally; they are intentionally not committed with media or credentials. Host rights-cleared output and a privacy-reviewed copy of `report.json`, `result.md`, `trace.jsonl` from each run, then add the exact public links to the README and PR description. If redacting personal local paths, label redacted copies and keep originals unchanged for review. **Do not mark the links complete until they can be opened without sign-in.**

## Video — not yet completed

The PR requires a demonstration of at most five minutes, including real working tasks and the impossible French refusal. Record and link the actual video before sending the final PR SHA. Explain clearly that the Tamil run covered only five pages and the Marathi QA had formatting issues.
