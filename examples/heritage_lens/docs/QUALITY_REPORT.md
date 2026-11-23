# Quality report (full-stack build)

Legend: **Implemented** = code written · **Executed** = actually run in the authoring sandbox ·
**Not executed** = written/compiled only · **Live verified** = real Bodhan call succeeded (none yet) · **Demo-only**.

## Environment limits of the authoring sandbox
No network: `pip`/`npm` installs were impossible. Missing there: FastAPI, SQLAlchemy, httpx, pytest, PyMuPDF, bcrypt,
Vite, React Router, Framer Motion, Recharts, Lucide, Leaflet, Vitest, Testing Library, TypeScript type packages.
FFmpeg, Node, React 19, esbuild and PyJWT were available.

## Test results
| Suite | Result |
|---|---|
| Backend pure-logic + service layer (capabilities, chunking, WAV/FFmpeg audio incl. MP3/OGG/FLAC/WebM, 30 s boundaries, validators, storage, redaction, all five Bodhan service layers with a fake client) | **Executed: 79 passed** (stand-in runner, not real pytest) |
| JWT round-trip / expiry / wrong secret / `alg=none` | **Executed** ad hoc |
| Backend HTTP/DB tests: client (httpx MockTransport) 12, auth 10, stories & ownership 8, media/AI routes 17, models 8, PDF 3, discovery/capsules/saved/dashboard 10 | **Not executed** (68 tests written; expect some first-run fixes) |
| Frontend logic + API client (validation, OCR text composition, recorder format choice, formatting, demo-data labelling, bearer header, error envelope, 401 signal, "only /api/ai" calls) | **Executed: 25 passed** (stand-in runner; same test files as Vitest) |
| Frontend component tests (badges, FileDrop validation, Recorder fallback, Demo/Live banner) | **Not executed** (need React Testing Library + jsdom) |
| Server-side render of all 14 pages + Layout with stubbed third-party libraries | **Executed: 16/16 rendered**, required home copy and demo-story content present. Proves no render-time crash on first paint; says nothing about effects, layout, Tailwind output, routing, Leaflet or Recharts |
| TypeScript type-check, `vite build`, ESLint | **Not executed** |
| Playwright end-to-end, responsive checks at 1440/1024/768/390/360 px, visual QA | **Not executed / not written** |
| Live Bodhan calls | **Not verified**: run `backend/scripts/verify_bodhan.py` |
| `setup-env.ps1` | **Not executed** (no PowerShell here) |

## Feature status
| Area | Status |
|---|---|
| Auth (register/login/me/profile, bcrypt, JWT, protected routes, ownership 404s) | Implemented · backend tests not executed |
| Stories CRUD, privacy, consent-gated publishing, filters/sort/pagination/facets | Implemented · not executed |
| Audio upload/record (MediaRecorder → FFmpeg), library, transcript edit | Implemented · FFmpeg paths executed, routes not |
| Transcribe / translate / transliterate / OCR / speak via backend only | Implemented · service layer executed with fakes · **not live verified** |
| Document Digitizer (preview, per-page OCR text, edit, download, delete) | Implemented · not executed |
| Script Explorer (language + Roman/Native, history) | Implemented · not executed |
| Map (Leaflet, contributor-supplied coordinates only), Timeline (never invents dates) | Implemented · not executed |
| Message to the Future (CRUD, voice message) | Implemented · **no automatic delivery or notifications** |
| Dashboard (real counts from DB, recharts) | Implemented · not executed |
| Saved heritage, images/photographs, public story bundle | Implemented · not executed |
| Demo Mode with labelled samples; Live Mode; persistent banner; provenance fields | Implemented · render-tested only |
| `/api/config/status` (presence only, never values) | Implemented · not executed |
| Try HeritageLens (8 guided examples) | Implemented · render-tested only |

## Known limitations
* Stock/cultural photography is not bundled (no network, licensing). Cards use a decorative terracotta pattern until a
  contributor uploads a photograph. Demo records have no photographs.
* No demo audio file is bundled (no licensed sample); Demo Mode says so.
* Muted text colour #8B756B is about 4:1 on Ivory, slightly under WCAG AA for small text; it is used only for secondary
  captions. Essential text uses Deep Clay. No automated accessibility audit was run.
* Not implemented: email verification, password reset, account deletion, login rate limiting, Alembic migrations,
  automatic capsule release, `/api/analytics` (the dashboard endpoint replaces it), Playwright tests, i18n of the UI itself.
* The token is kept in `localStorage` (simple, but exposed to any XSS); consider httpOnly cookies before production.
* Chunk sizes and OCR-502 handling rest on documented assumptions (see `backend/docs/BODHAN_API_SPECIFICATION.md`).
* Database tables are created on startup. Changing a model later requires a migration; there is no Alembic setup yet.
  (An older dev database file created before the `event_year` columns were added must be deleted.)
