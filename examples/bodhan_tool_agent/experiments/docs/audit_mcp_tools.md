# Audit: MCP tool descriptions & schemas as a third-party client sees them (in-session rerun, 2026-11-19)

Scope: `src/bodhan_mcp/server.py` (live FastMCP tools),
`src/bodhan_agent/agent.py` (model-facing catalog, `_get_openai_tool_definitions`),
`experiments/data/tool_catalog_snapshot.json` (what the routing benchmark offers),
`tests/test_mcp.py`, `tests/test_catalog_guard.py`.

## Verdict

**Above average for agent-of-one usage; not yet a third-party-grade tool surface.**
The paid-model tools carry genuinely strong routing contracts (nearest-neighbour
boundaries, cost-sensitive rules, unsupported-voice negatives). The weak spots:
(1) **cost is invisible** — nowhere in any tool docstring is ₹ pricing stated, so
planned budgeting is blind; (2) **provenance is unenforced** — `write_text_file`
cannot tell an Indic-language string the agent may not compose from one that
came from a Bodhan tool; (3) **`detect_language` lies usefully for code-mixed
script**; (4) control tools (`submit_plan`, `finish`) are near-tenants — no
boundary language at all, and the routing data treats them as first-class.

## Snapshot vs live — two checks, one real

Tool descriptions in the snapshot are **whitespace-normalised identical** to the
live docstrings for all 8 MCP tools (verified programmatically; earlier "missing
in src" hits for the 4 paid tools were my regex tripping on the intervening
`@indic_cache(...)` decorators at `server.py:350`, `619`, `815`, `949`).

The **parameters differ deliberately**: snapshot drops `bypass_cache` from all
four paid tools via `_HIDDEN_SCHEMA_PARAMS` (`agent.py:217-219`). Design is
coherent (hide a paid-redo foot-gun) but:
- it is **undocumented** in the README/snapshot header,
- nothing enforces that the agent's dispatch path rejects an unexpected
  `bypass_cache` argument slipped in by a confused client, and
- `refresh_catalog_snapshot.py` reads from the agent's tool definitions, so the
  snapshot can silently drift from the *server* schemas if the two change
  independently. It checked name sets, not parameter schemas.

`tests/test_catalog_guard.py` should assert full JSON Schema equality (post
exclusion) so this is mechanical, not aspirational.

## Per-tool scorecard (clarity / schema / limits / errors / total /10)

| Tool | Clarity | Schema | Limits | Errors | Total | Top fix |
|---|---|---|---|---|---|---|
| transcribe_audio | 9.0 | 8.0 | 8.5 | 8.5 | **8.5** | State ₹0.10/min + never call when only metadata is needed; note `.srt/.vtt` output is already direct, no downstream translate needed for same-language subtitles (doc implies) |
| translate_text | 8.5 | 8.5 | 8.0 | 8.5 | **8.4** | State ₹0.20/10k output tokens; list *exact* supported code universe (it does); name the translate-vs-transcribe when-English confusion explicitly ("speech + English subtitles → transcribe_audio(task='translate'), NOT translate_text") |
| synthesize_speech | 8.0 | 7.5 | 6.0 | 8.5 | **7.5** | Add: ₹6/10k chars, voice names are **only** the catalog (Kavya/Amit/…; see `bodhan://catalog/voices`), **no max input guidance** — a 25k-char chapter fires ~10+ paid calls with no approval gate; say so, or bound it |
| extract_document_text | 8.5 | 8.0 | 7.0 | 9.0 | **8.1** | ₹0.20/**page** (images = 1 OCR page each) — a 40-page PDF is 40 images billed; say what `page_limit` does on lossy/scanned PDFs and that corrupted PDFs raise ToolError with guidance |
| inspect_media | 8.5 | 9.0 | 9.0 | 8.5 | **8.8** | Minor: say nothing about needing ffprobe — or remove claim, returns: duration/rate/channels/pages |
| read_text_file | 9.0 | 9.0 | 9.0 | 9.0 | **9.0** | Document the hard `limit` cap (or state "unbounded, defaults 10,000"), confidence note on .docx char-count consistency vs server's extractor |
| write_text_file | 7.0 | 7.5 | 8.5 | 8.0 | **7.8** | **P1: Indic provenance**. Currently rejects audio by extension only — the single most dangerous gap in the assignment (LLM-authored Indic text). Add a `transcript`/`translation`/`extract` **source binding** or provenance marker requirement for Indic scripts, and refuse Indic-script content unless a marker/justification env is present |
| detect_language | 7.0 | 8.0 | 8.0 | 7.0 | **7.5** | **Code-mixed inputs**: Hinglish/Tanglish in Latin script is reported as Latin-without-Indic. That is the exact blind spot the agent must catch. Document it, and consider emitting a `code_mix_detected` signal (both Latin + Indic blocks present) |
| submit_plan | 5.0 | 6.0 | — | — | **5.5** | Add cost-budget arg? At least: "`feasibility` must be false with reason if language unsupported/audio cloning/out-of-workspace; assumptions go verbatim into result.md" — guide the contract, don't just name it |
| finish | 5.0 | 6.0 | — | — | **5.5** | Same: define status semantics in 1-1 correspondence with `validation_report.json`'s rules (impossible/failed/completed); "answer MUST state unverified/untested, never imply success" |

Operator-only tools (`get_cache_stats`, `clear_cache`): correctly hidden from
the model, keep as-is.

## Reading for the Indic-work angle

These are *this task family's* contracts and the docs/help prose currently read
like generic SaaS API docs plus routing hints. Missing for Indian-language
knowledge work:

1. **₹ pricing everywhere it is a paid call** (transcribe/translate/speak/OCR).
   Without it, `--max-cost-inr` is enforced but never *planned against* — the
   agent literally cannot know ahead of time whether ₹10 covers the chapter.
2. **Provenance is the assignment's automatic-fail boundary.** Update
   `write_text_file` (and, at the agent layer, the prompt + verifier) so
   Indic-script text in an artifact must come from a tool result — see
   `audit_agent.md` P0-2/P0-3.
3. **Code-mixing** deserves a documented path: transcribe stays verbatim (already
   correct), but `detect_language` returning "Latin" on Hinglish is actively
   misleading. Fail honestly, don't silently route to English.
4. **Shared-script ambiguity** (`ks/sd/mni` subtags, Devanagari shared by
   hi/mr/ne/sa/bm/bho/kok) is documented for translate_text — good — but
   `transcribe_audio(language=...)` doesn't say what happens when a Marathi
   clip is labelled Hindi (silently accepted) — fine, but *where* that surfaces
   (result quality, not error) is undocumented.
5. **Register & numerals**: nothing about Indic numerals (০-৯ etc.), Danda '।'
   sentence splitting (already implemented — not documented), formal/colloquial
   target choice — for knowledge work the *output register* is part of
   correctness and is currently undocumented and unscoped.

## Prioritized fixes

**P0 (third-party-client usability gate — do before demo/submission):**
1. Add one ₹ cost hint per paid-tool doc (transcribe/translate/synthesize/ocr);
   mirror to README pricing table; expose `bodhan://catalog/pricing` resource if
   cheap.
2. `write_text_file` Indic provenance contract + agent-side enforcement
   (`audit_agent.md` P0-2).
3. `finish`/`submit_plan` semantics in their tool descriptions, aligned with
   the report-rubric status rule; verification status must be *reported*, never
   implied.

**P1 (quality/integrity tier):**
4. `detect_language` doc: state Latin/Indic code-mix behaviour; return
   `code_mixed` signal or say "dominant block only".
5. `synthesize_speech`: bound/total-cap guidance + `bodhan://catalog/voices`
   pointer (resource already exists, unadvertised in doc).
6. `extract_document_text`: per-page cost warning; `page_limit` semantics.
7. `tests/test_catalog_guard.py`: assert full JSON-schema equality, post
   `_HIDDEN_SCHEMA_PARAMS` exclusion; drop or document the agent/server
   dual-source inconsistency silently ignored today.

**P2:**
8. `inspect_media`: state absence of ffprobe dependency (or its actual
   requirement).
9. Cross-tool disambiguation one-liners where routing actually errs
   (transcribe-translate vs translate_text; write vs synthesize; read_file vs
   extract) — the descriptions mostly have this, the agent prompt and docs
   gloss over it; keep all three in sync via one source of truth.

Snapshot-vs-live mismatch fix, if any doc text changes, *must* run
`experiments/scripts/refresh_catalog_snapshot.py` and re-freeze the routing
benchmark's `dataset_sha256` in lock-step — the routing suite binds to both.
