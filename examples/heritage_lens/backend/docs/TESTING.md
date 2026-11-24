# Testing

| Suite | Needs | Status in the authoring sandbox |
|---|---|---|
| test_capabilities, test_text_chunking, test_wav_audio, test_validators_storage, test_logging_redaction, test_bodhan_services (79 tests) | stdlib + FFmpeg | **Executed: 79 passed** with a minimal stand-in runner (real pytest was not installable offline) |
| JWT round-trip / expiry / wrong secret / alg=none | PyJWT | **Executed** ad hoc (not in the suite) |
| test_bodhan_client (httpx MockTransport), test_auth_api, test_stories_ownership_api, test_media_ai_api, test_models, test_pdf | fastapi, sqlalchemy, httpx, pytest-asyncio, bcrypt, PyMuPDF | **Written and compiled, NOT executed** (dependencies unavailable offline) |

Run everything locally with `pytest -v`. Expect to fix small issues on the first real run: these files were
never executed. Mocked tests prove our payloads and parsing, not Bodhan's behaviour; only
`scripts/verify_bodhan.py` output counts as live verification.
Frontend, Playwright and responsive tests belong to later stages.

## Update (full-stack build)
Added `test_discovery_api.py` (10 tests, config status, filters/facets, bundle privacy, public images, locations, timeline,
capsules, saved stories, dashboard). Total backend tests: 79 executed (stand-in runner) + 68 written, not executed.
Frontend: `src/test/*.test.ts(x)`; 25 logic/API tests executed with a stand-in runner, 5 component tests not executed.
See `../docs/QUALITY_REPORT.md`.
