# Architecture (Stage 1 + 2)

Layers: routes (`app/api/routes`) -> services (`app/services/*`) -> storage/DB/Bodhan. Routes do auth,
ownership and persistence; services are framework-free (stdlib only for audio, text, validation, storage,
and the five Bodhan service functions, which talk to an injected client). That is why those layers are
testable without FastAPI.

Key rules
* **Originals are immutable.** AI output goes in separate columns/rows (`transcript`, `translated_text`,
  `extracted_text`) and user corrections in `*_edited_*` columns. Nothing AI-generated writes to
  `heritage_stories.original_text`, an original audio path or a stored scan.
* **No fake results.** Unconfigured -> 503 `ai_service_not_configured`; upstream failure -> 4xx/5xx with a
  safe message. Responses carry `provenance: "live_bodhan"`.
* **Ownership**: other users' private rows return 404 (existence not revealed). Public listing =
  `privacy=public AND publication_status=published`; publishing requires `consent_confirmed`.
* **Files**: random server-chosen names under `storage/users/<id>/<category>/`, outside any static path;
  type decided by magic bytes; path-traversal and symlink escapes blocked; served only via authenticated routes.
* **Errors**: one envelope `{"error": {code, message, request_id, details?}}`; validation errors never echo input.
* **Logging**: redaction filter removes registered secrets, bearer tokens and `password=`/`api_key=` patterns.

Sync SQLAlchemy sessions are used inside async AI routes (short queries); FFmpeg and PDF rendering run via
`asyncio.to_thread`. Tables are created at startup for development; add Alembic before production.
