# Bodhan Smoke Report: transliterate_hindi_roman_01

- **Case ID:** `transliterate_hindi_roman_01`
- **Task:** Transliterate the inline Hindi sentence `नमस्ते, आप कैसे हैं?` from Devanagari to Latin/Roman script without translating it.
- **MCP call attempted:** Yes
- **MCP call succeeded:** Yes
- **Tool:** `bodhan-setu-transliterate_text`
- **Arguments:** `{"language":"hi","script":"roman","text":"नमस्ते, आप कैसे हैं?","dry_run":true,"estimated_output_tokens":100}`
- **Exact returned result:** `{"dry_run":true,"model":"indic-transliterate","quantity":100,"unit":"output_token","estimated_cost_inr":0.002,"rate":{"per_10k_output_tokens":0.2},"quantity_basis":"caller estimate","will_call_api":false}`

## Server evidence

The started MCP server process was present as `/Library/Frameworks/Python.framework/Versions/3.11/Resources/Python.app/Contents/MacOS/Python -m bodhan_mcp` (PID 470, started 2026-11-18 20:22:52). No persistent MCP log was found; the returned MCP tool result above is also server-response evidence.

## Verifier criteria

- `uses_indic_transliterate_not_translate`: **PASS** - the configured transliteration tool returned model `indic-transliterate`.
- `dry_run_precedes_paid_call`: **PASS** - `dry_run=true` and `will_call_api=false`; no paid call was made or needed.
- `roman_script_output`: **UNVERIFIED** - the dry run returned only a cost estimate and no transliterated text.
- `same_hindi_language_preserved`: **UNVERIFIED** - no linguistic output was produced by the dry run.
- `no_meaning_translation`: **UNVERIFIED** - no linguistic output was produced by the dry run, so preservation could not be checked.

## Paid calls and cost

- Paid Bodhan calls: **0**
- Paid cost: **0 INR**
