# v3 migration notes — 2026-11-19 (Bodhan recipe, in-session consolidated)

Status as of the end of the 2026-11-19 session. All changes uncommitted; no git operations were made.

## Snapshot of what is true now

- **Benchmark v3** (`experiments/data/`): 87 cases; splits 37 train / 21 dev / 29 test; categories 21/19/14/17/9/7;
  difficulty hard/medium/easy = 20/42/25; 10-tools & 6-categories test coverage; declaratory split quota discipline
  unchanged. `validation_report.json` regenerated; `issues: []`; dataset sha-256:
  `2c3abeefd189128415a7efd7ff67826d8099e2c98630c39edd83b67ae92a8eb4` (binds case set; snapshot refresh does not
  change it). README rewritten for v3; old 70/76 claims corrected.
- **Track A (tool surface)** — ₹ cost lines on the 4 paid tools, provenance doc text for `write_text_file`,
  `detect_language` code-mix warning, `extract_document_text` `page_limit` semantics + mixed-billing honesty,
  catalog-guard test now asserts **advertised catalog vs live schema** (16 tests).
- **Track B (agent runtime)** — `_verify_outputs`: zero-artifact ⇒ `verified=None`, `status="not_applicable"`
  (never vacuously true). Text artifacts with Indic content get a `text_provenance` check (≥70% Indic-char coverage by
  the run's Bodhan outputs). `write_text_file` dispatch guard refuses LLM-authored Indic text when not derived from
  Bodhan outputs (deterministic, trace-marked `provenance_guard`). transcribe cache key no longer includes
  `output_path`; cache replays now materialize the file at a caller-specified `output_path`. Agent-layer per-call
  timeout (`AGENT_TOOL_TIMEOUT_S`, default 300 s). `tests/test_e2e.py` restructured so the "LLM writes Marathi" test
  no longer codifies the automatic-fail behavior; 3 new regression tests.
- **Track C (docs)** — stale v2/70/76 references fixed under `experiments/**`; notebook and optimization notes
  carry the pre-v3 caution; root README stale counts corrected by the lead (matrix now **155 passing** in 14 modules,
  collected = 155).
- **Track D (API outage resilience)** — shared transport-error classifier in `server.py` for Transcribe/Translate/
  Speak/OCR: ConnectTimeout/ConnectError/ReadTimeout/WriteTimeout/PoolTimeout + HTTP 429/5xx bounded retry
  (3 attempts, backoff+jitter, ~20 s wall-clock budget; 429 Honors one sane `Retry-After` ≤ 30 s); 4xx other than 129
  raise immediately; on exhaustion the `ToolError` names the provider base_url, the exception class, and forbids
  producing Indic text via the LLM. Agent dispatch carries an outage-breaker (`AGENT_API_OUTAGE_BREAK`, default 3
  consecutive outage observations) → run stops, trace logs `tool=agent status=error`, final answer preserves the
  verbatim English draft and reports `failed` with exactly what was and wasn't produced. **6 new retry tests + 1 e2e
  outage-breaker test pass.**
- **API health (2026-11-19T07:06Z UTC)** — `experiments/09_bodhan_api_health_check.ipynb`: DNS/TLS ✅; unauthenticated
  calls return 401/404 as expected; authenticated minimal calls all pass in ~0.6–1.8 s; `/translate` (root legacy
  fallback) is HTTP **404 on production** — dead legacy path.

## Bug born & fixed in this session

`httpx.Timeout(connect=5.0)` on the translate path with the old 4-attempt retry (per call site) → **exact 23.5–23.6 s
per failure**. Pre-existing, configured, not a leak. After Track D, the wall-clock bound per paid call is ~20 s with
identical-or-better honesty. `connect=5.0` was **not** changed; consider raising/tuning only with deliberate
review of the demo's latency targets. The current contract is `connect=30.0`, `read=120.0`, budget 120 s.

**The deeper 2026-11-19 defect (masked by intermittent ConnectTimeouts): `_call_transcribe` always sent `task`
in the multipart body — the live endpoint rejects ANY `task` with deterministic **HTTP 422** (litellm: `unexpected
field(s): ['task']`). Every transcription call through the recipe would 422 no matter the network window. Fixed by
not sending `task`; `task='translate'` is honored by transcribing in the source language, then translating via
Indic-Translate (existing fallback in `server.py`), preserving SRT/VTT timecodes. Verified live: the Hindi monologue
that failed at 14:38Z now produces an English `.srt` end-to-end. Two independent failure modes therefore coexist:
(1) intermittent route death (SYNs dropped from this machine during 08:43Z and 14:38Z windows) and (2) a
deterministic 422 on every `task`-carrying transcription request, fixable in code. This fix addresses (2); (1) is not
fixable in client code — it is only roadmappable by running when the route is green.

`experiments/10_api_failure_reproduction.ipynb` is the replayable artifact: TCP probes → exact failing translate
payload → recipe tools with the real PDF and the Hindi monologue — all passing now; includes the HTTP-422 probe matrix
that confirmed the defect deterministically.

## Verified counts (do not re-derive)

- `pytest tests/ -q` → **155 passed**, 5 warnings. `test_mcp.py::test_transcribe_audio_task_forwarding` is flaky
  in isolation only (machine-wide, content-addressed `indic_cache.db` reuses the synthetic test WAV across runs; passes
  in the full suite due to `test_cache.py` clearing the shared DB first). Set `BODHAN_CACHE_ENABLED=false` if running
  the file standalone.
- Snapshot refreshed 2026-11-19 after D; dataset rebuilt after A/D → sha unchanged `2c3abeef…` in lockstep.

## Still open — read before claiming victory

1. **The v3 benchmark has never been used to score the agent.** The routing-evolution table in the root README is a
   12-case pre-v3 pilot; v3 (or the hidden adversarial run) is the first honest signal. Do not quote 100% as acceptance.
2. **The root `DEMO_VIDEO_SCRIPT.md` received Appendix A (2026-11-19)** — it flags the 12-case pilot vs v3, the 155-test count, the Indic-provenance guard, outage-breaker and the health-check evidence; do not record the script's stale "121 passed" / "35-case GEPA / 100%" lines without adopting those corrections. `exp08` outputs are relabeled in-place with dated corrections (`review_taste_calls.md`, `export/optimization_report.md`); frozen raw JSON logs (baseline_pilot.json, test_results.json, optimization_report.json, run1_*) still reference v2-era `K-01` ids and are intentionally unaltered.
3. **Synthesize HTTP 500s** observed when `instructions` omitted; style `"formal"` listed in the spec but returns 500
   on live (English input). Treat the published style catalog as indicative only; server code passes `style`
   verbatim, so unsupported styles will 500 end-to-end (Track D turns that into bounded retry + honest failure, not a
   fabricated artifact).
4. `.env` LLM route is `https://api.openai.com/v1` with `LLM_MODEL=gpt-6-luna` — the rubric hidden run overrides this;
   local demo performance does not generalize to their provider.
5. `synthesize_speech` has **no approval gate or total cap**; a long document still fans out into one paid call per
   sentence (Semaphore(4)). The cost ledger counts it; the breaker/budget halts it — nothing stops it a priori.
6. `.pytest_cache/` churn in the recipe root was accepted knowingly; no source files reference it.
7. The record best describes degraded-service handling, not "Bodhan was down". For the PR/demo, cite the health
   notebook results, not a live outage claim.

## What two tracks did NOT do (deliberately)

- A: docstring-only edits; no limit-enforcing code changes (that's B/D).
- C: no edits under `outputs/**` or `_archive/**` (frozen evidence), no edits to frozen 000-008 notebooks' code,
  no git operations.

## If you resume this work, do in order

1. `pytest tests/ -q` → expect 155 passed (isolate the flaky `test_transcribe_audio_task_forwarding` if it fails alone:
   `BODHAN_CACHE_ENABLED=false`).
2. Re-run `07_*`/`08_*` notebooks (or a small v3 re-scoring pilot) before quoting v3 accuracy.
3. Decide the PR text for routing evolution (pilot vs v3) — one line each; do not invent a v3 number.
4. Update the demo script to reflect: v3-merged hardening, `AGENT_API_OUTAGE_BREAK` resilience, and the health-check
   notebook as evidence instead of "503 outage" claims.
