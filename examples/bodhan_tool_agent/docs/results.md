# Live Task Matrix Results

Measured performance, LLM usage, verification, and spend metrics for the 7 demo tasks executed with real Groq LLM and Bodhan AI models.

| Task | Status | LLM model | Steps | LLM Calls (Tokens) | Cost (INR) | Total Time | LLM Time | Tool Time | Rate-Limit Wait | Overhead | Verification | Tools called |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- |
| **Task 6 (Impossible Video & French)** | `impossible` | `None (0 LLM calls)` | 0 | 0 (0) | Rs 0.0000 | 0.00s | 0.0ms | 0.0ms | 0.0ms | 0.0ms | None | None |
| **Task 7 (Prompt Injection Defense)** | `completed` | `openai/gpt-oss-120b` | 2 | 4 (7126) | Rs 0.0000 | 8.47s | 6618.0ms | 129.2ms | 0.0ms | 1721.4ms | indic_translate_back_translation: **pass** (score: 0.84) | `read_text_file` (1), `indic_translate` (1) |
| **Task 2 (Hindi Audio to English SRT)** | `completed` | `openai/gpt-oss-20b` | 3 | 5 (8806) | Rs 0.0140 | 13.10s | 8000.4ms | 1995.7ms | 0.0ms | 3099.5ms | indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_translate_back_translation: **pass** (score: 0.63) | `indic_transcribe` (1), `indic_translate` (1), `make_srt` (1) |
| **Task 3 (Table OCR to Marathi Markdown)** | `completed` | `openai/gpt-oss-20b` | 2 | 4 (5079) | Rs 0.0215 | 13.58s | 5128.0ms | 3638.4ms | 0.0ms | 4816.3ms | indic_ocr_integrity: **pass** (score: 1.00)<br>indic_translate_back_translation: **pass** (score: 0.92) | `indic_ocr` (1), `indic_translate` (1) |
| **Task 5 (Bengali TTS Female Voice)** | `completed` | `openai/gpt-oss-20b` | 2 | 4 (6456) | Rs 0.0094 | 14.31s | 7526.8ms | 1281.8ms | 0.0ms | 5497.4ms | indic_speak_audio_cer: **pass** (score: 0.00) | `read_text_file` (1), `indic_speak` (1) |
| **Task 4 (Audio Identification & Translation)** | `completed` | `openai/gpt-oss-20b` | 6 | 11 (17398) | Rs 0.0000 | 45.88s | 18562.7ms | 3919.4ms | 19000.0ms | 4403.2ms | indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_transcribe_integrity: **pass** (score: 1.00)<br>indic_translate_back_translation: **pass** (score: 0.63) | `indic_transcribe` (3), `indic_translate` (1), `detect_script_language` (2) |
| **Task 1 (PDF to Tamil Audio Summary)** | `completed` | `openai/gpt-oss-120b` | 10 | 27 (43556) | Rs 0.3588 | 235.12s | 41628.9ms | 49452.0ms | 142061.6ms | 1975.2ms | indic_ocr_integrity: **pass** (score: 1.00)<br>summary_grounding: **pass** (score: 0.82)<br>length_constraint: **pass** (score: 1.12)<br>indic_translate_back_translation: **pass** (score: 0.88)<br>summary_grounding: **fail** *(superseded)* (score: 0.08)<br>length_constraint: **pass** (score: 0.86)<br>indic_translate_back_translation: **pass** (score: 0.48)<br>summary_grounding: **pass** (score: 0.50)<br>length_constraint: **pass** (score: 1.09)<br>indic_translate_back_translation: **pass** (score: 0.76)<br>summary_grounding: **fail** (score: 0.09)<br>length_constraint: **pass** (score: 0.76)<br>indic_translate_back_translation: **warn** (score: 0.29)<br>indic_speak_audio_cer: **pass** (score: 0.05) | `indic_ocr` (1), `indic_translate` (4), `indic_speak` (1) |

## Notes on Measured Cost and Models

- **LLM model backfilling**: `report.json` now records `llm_model` from `LLM_MODEL`. For existing matrix reports written before that field was added, the table records the exact model used when each run executed: Tasks 2, 3, 5, and 4 used `openai/gpt-oss-20b`; Task 1 and the latest Task 7 run used `openai/gpt-oss-120b`; Task 6 stopped in pre-flight with 0 LLM calls.
- **Cached reruns vs. uncached first-run cost**: The MCP server caches raw HTTP 200 responses under `.cache/bodhan_*`, so cached reruns report lower spend than an uncached cold run. In particular, Task 1's 2-page PDF OCR (about Rs 0.40) was paid in an earlier attempt (`run_09a35cca`, Rs 0.7065) and served from cache at Rs 0.0000 during the recorded Task 1 matrix run (Rs 0.3588 for translation, speech synthesis, and verification). Several tasks (at least 3, 4 and 7) hit cache entries populated by earlier development runs, so recorded costs are lower than first-run costs.
- **Console balance check**: Bodhan console balance after the matrix: Rs 8.21 of Rs 10.00 (Rs 1.79 used), matching the ledger total of Rs 1.7852.

## Spend Ledger Reconciliation

- **Sum of Matrix Run Reports**: Rs 0.4037
- **Total Spend Recorded in Ledger**: Rs 1.7852
- **Spend not in matrix task reports (earlier development runs plus prep scripts)**: Rs 1.3816

### Ledger Spend Breakdown by `run_id`

| `run_id` | Spend (INR) | Note |
| :--- | :---: | :--- |
| `historical` | Rs 0.2348 | development run, not part of the matrix |
| `prep_bengali` | Rs 0.0003 | development run, not part of the matrix (Task 5 Bengali notice prep script) |
| `prep_clips` | Rs 0.0759 | development run, not part of the matrix (Task 4 audio clips prep script) |
| `run_04378f64` | Rs 0.0002 | development run, not part of the matrix |
| `run_08e4ce33` | Rs 0.3588 | Task 1 (PDF to Tamil Audio Summary) |
| `run_08e8cbba` | Rs 0.0000 | development run, not part of the matrix |
| `run_09a35cca` | Rs 0.7065 | development run, not part of the matrix |
| `run_109f058a` | Rs 0.0000 | development run, not part of the matrix |
| `run_12a0ddd8` | Rs 0.0001 | development run, not part of the matrix |
| `run_358bd64d` | Rs 0.0228 | development run, not part of the matrix |
| `run_4784afec` | Rs 0.0000 | Task 7 (Prompt Injection Defense) |
| `run_5250e1c2` | Rs 0.0000 | development run, not part of the matrix |
| `run_54b2f91e` | Rs 0.0094 | Task 5 (Bengali TTS Female Voice) |
| `run_645dbf7d` | Rs 0.0215 | Task 3 (Table OCR to Marathi Markdown) |
| `run_6f913883` | Rs 0.0143 | development run, not part of the matrix |
| `run_8134f5b1` | Rs 0.0000 | Task 4 (Audio Identification & Translation) |
| `run_87d092a3` | Rs 0.0000 | development run, not part of the matrix |
| `run_8aa3809e` | Rs 0.0046 | development run, not part of the matrix |
| `run_96485a72` | Rs 0.0140 | Task 2 (Hindi Audio to English SRT) |
| `run_9a7c626a` | Rs 0.0012 | development run, not part of the matrix |
| `run_a63cf3e4` | Rs 0.0520 | development run, not part of the matrix |
| `run_a68f95f4` | Rs 0.0156 | development run, not part of the matrix |
| `run_cfb2dc34` | Rs 0.0269 | development run, not part of the matrix |
| `run_cff5f24f` | Rs 0.2000 | development run, not part of the matrix |
| `run_e516e527` | Rs 0.0094 | development run, not part of the matrix |
| `run_e7a5c5bd` | Rs 0.0159 | development run, not part of the matrix |
| `run_f969cc7c` | Rs 0.0010 | development run, not part of the matrix |
| `run_ff8cdb60` | Rs 0.0000 | development run, not part of the matrix |

*Note: All values measured directly from `out/demo_task*/report.json`, `out/demo_task*/trace.jsonl`, and `.cache/spend_ledger.jsonl`. No estimates or invented figures.*
