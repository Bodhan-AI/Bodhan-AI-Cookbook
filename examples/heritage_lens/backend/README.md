# HeritageLens backend (Stage 1 + Stage 2)

FastAPI + SQLAlchemy backend with JWT auth, private file storage, and a hardened client for the five
permitted Bodhan services (`indic-transcribe`, `indic-translate`, `indic-transliterate`, `indic-ocr`,
`indic-speak`). No other AI provider is used. There is no demo fallback: if a service is not configured or
fails, the API says so.

## Prerequisites
* Python 3.11+ (developed on 3.12)
* **FFmpeg** on PATH (or set `FFMPEG_PATH`). Needed for every audio upload and transcription.
  Windows: `winget install Gyan.FFmpeg`, then open a new terminal and check `ffmpeg -version`.

## Run on Windows PowerShell
```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements-dev.txt
Copy-Item .env.example .env
notepad .env                           # set SECRET_KEY and BODHAN_API_KEY (never commit .env, never paste keys in chat)
python -c "import secrets; print(secrets.token_urlsafe(48))"   # a good SECRET_KEY
uvicorn app.main:create_app --factory --reload
```
Open http://127.0.0.1:8000/docs (disabled when `APP_ENV=production`) and http://127.0.0.1:8000/api/health.

## Test
```powershell
pytest -v                              # full suite (needs requirements-dev.txt)
python scripts/verify_bodhan.py        # LIVE check of all five services (uses a few credits)
python scripts/verify_bodhan.py --only translate,transliterate --json report.json
```
`verify_bodhan.py` prints PASS / FAIL / SKIPPED per service and never prints keys. Mocked pytest results
are **not** live verification.

## Configuration
See `.env.example`. One shared `BODHAN_API_KEY` is used for all services unless a per-service
`BODHAN_<SERVICE>_API_KEY` is set. The docs do not say whether one key is scoped to every model; the
verify script settles that empirically.

## Layout
`app/core` config, security, errors, logging · `app/database` engine/base/types · `app/models` tables ·
`app/schemas` Pydantic · `app/api` deps + routes · `app/services/bodhan` the five integrations + client +
capability tables · `app/services/audio` WAV + FFmpeg + segmentation · `app/services/documents` PDF render ·
`app/services/storage` validation + private store · `tests/` · `scripts/verify_bodhan.py`.

See `docs/` for architecture, database, the Bodhan specification as read, testing and the status report.
