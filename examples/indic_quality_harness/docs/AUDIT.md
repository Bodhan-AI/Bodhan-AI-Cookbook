# Audit: requirements vs implementation

State **before** this change, what was changed, and how it is tested. The
assignment brief was received truncated partway through section 4
("Calibration and Error Injection"). Requirements after that point were
not available and are not claimed here.

Legend: ✅ done and tested · ⚠️ done with a documented limitation.

## CLI flags

| Flag | Before | Now | Tests |
|---|---|---|---|
| `--dataset`, `--langs`, `--out` | Worked | Unchanged. A missing file exits with code 2 | `test_pipeline::test_cli_*` |
| `--calibration` | Worked (reference-based only) | ✅ Three modes: reference-based, reference-free, LLM judge. Clean-case false-alarm rate | `test_calibration_extended`, `test_pipeline::test_cli_offline_with_calibration` |
| `--back-translate` | Number check only. No cache or budget | ✅ Numbers, chrF and content-word coverage, entities, truncation. Cached, budgeted, rate-limited | `test_pipeline::test_back_translation_uses_source_and_caches` |
| `--speech` | Worked. Cache key missed `instructions` and VAD. Hindi voice for every language | ✅ Full-input cache key, per-language voice, coverage and truncation checks, frame silence, duration vs text | `test_speech_extended`, `test_pipeline::test_speech_*` |
| `--speech-limit` | **Bug**: limited the whole run, not just speech | ✅ Limits speech only, round-robin by language | `test_pipeline::test_speech_limit_restricts_only_speech` |
| `--llm-judge` | **Missing** from the CLI | ✅ Integrated, lazy, review-only, cached | `test_llm_judge_offline`, `test_pipeline::test_llm_judge_*` |
| `--generate` | Parsed, **unused** | ✅ Translates records with no translation. Never overwrites, never used as a reference | `test_pipeline::test_generate_fills_missing_translation_only` |
| `--baseline` | Parsed, **unused** | ✅ Regressions, fixes, category deltas | `test_pipeline::test_baseline_comparison` |
| `--max-cost-inr` | Parsed, **unused** | ⚠️ Pre-call budget guard and spend estimate. Default rates are placeholders | `test_pipeline::test_budget_*` |
| new: `--voice`, `--judge-model`, `--ollama-url`, `--judge-timeout`, `--request-interval`, `--cache-dir` | none | ✅ | covered indirectly |

## Translation evaluation

| Requirement | Before | Now |
|---|---|---|
| Number preservation | Order-sensitive. 5/5 failures on demo_100 were false positives | ✅ Multiset, scale words (lakh/crore/million), Indian grouping, spelled-out numbers → review. 0 false failures on demo_100 |
| Date / time / unit preservation | Missing | ✅ `date_time_mismatch`, `unit_mismatch` (lexicon-based, ⚠️ hi/ta/bn only) |
| Entity and terminology | Missing | ✅ `entity_mismatch` (e-mail, URL, places, Latin names), `terminology_mismatch` (per-record glossary) |
| Markdown integrity | Marker counts only | ✅ Plus `markdown_structure`: headings, lists, tables, link URLs, code |
| LaTeX integrity | `$` counts only | ✅ `latex_integrity`: verbatim math, commands, braces, environments |
| Reference-based comparison | Token diff (adds every replacement twice) | ✅ `reference_chrf` for independent references. Diff fixed and used only for calibration |
| Reference-free via back-translation | Numbers only | ✅ See above |
| Generated translation never a reference | n/a | ✅ Enforced by design and tested |

## LLM judge

| Requirement | Status |
|---|---|
| Not required without the flag | ✅ Test asserts `OllamaJudge` is never constructed |
| Pydantic structured validation, existing categories and severities | ✅ Unchanged models, `extra="forbid"` |
| Evidence spans preserved. Omission from the source, others from the translation | ✅ Per-finding validation. The prompt's earlier contradiction ("always quote the translation" vs "omission quotes the source") is fixed |
| Invalid evidence rejected without losing valid findings | ✅ Per-finding parsing (previously one invalid category failed the whole response) |
| Judge only, never rewrites | ✅ Prompt, schema, and dropped extra fields |
| Temperature 0 | ✅ Plus seed 0. Tested on the request payload |
| Malformed output, timeouts, Ollama down | ✅ Status values, availability check at start, disabled after the first outage |
| Persistent cache keyed by model, prompt version and hash, source, translation, language | ✅ Plus decoding options. Raw output cached and re-validated on read |
| Tests: valid, clean, invalid category, invalid span, wrong evidence source, cache hit, cache miss, unavailable, malformed | ✅ All in `test_llm_judge_offline.py` |
| Accuracy discussion | ✅ `TECHNICAL_GUIDE.md` §8 and the calibration report |

## Calibration and injection

| Requirement | Status |
|---|---|
| Omission, addition, mistranslation, number/unit, entity, formatting | ✅ Plus repetition. `unit_mismatch` is separate from `number_mismatch` |
| Builder for a labelled set | ✅ `python -m bodhan_eval.build_calibration` |

## Other fixes found during the audit

* The live Ollama test failed whenever Ollama was not running. It now skips.
* `pydantic` was unpinned and missing from `pyproject.toml` (validator error).
* `.env.example` listed `BODHAN_API_KEY` only, while the code read three
  other variables. Both are now supported: service-specific key first, then
  the shared key.
* No 429 handling. Now: pacing, bounded back-off, circuit breaker.
* A stage error in one record no longer aborts the run.
* README and notebook were still the template.

## Not changed (deliberately)

* `bodhan.py` still calls Indic-Translate through `/v1/chat/completions`.
  `scripts/bodhan_api_rules.json` lists `POST /translate` as the endpoint,
  but the existing path works live (verified) and the brief says to
  preserve it. Worth confirming with Bodhan which endpoint is canonical.
* `generate_dataset.py` (the original demo_100 generator) is untouched.
