# Deployment (not yet exercised: review each step)

**What you need**: a Linux VM (or similar), Python 3.11+, FFmpeg, Node 20+ (build only), PostgreSQL recommended,
HTTPS (browsers only allow microphone access on HTTPS or localhost), a persistent disk for uploads.

1. **Configuration** (`backend/.env` on the server, never in Git):
   `APP_ENV=production`, `SECRET_KEY` (>= 32 random chars; startup fails otherwise), `DATABASE_URL`
   (e.g. `postgresql+psycopg://user:pass@host/heritagelens`; add `pip install "psycopg[binary]"`),
   `STORAGE_DIR=/var/lib/heritagelens/storage`, `CORS_ORIGINS=https://your-domain`, and the Bodhan keys.
   In production `/docs` and the OpenAPI schema are disabled.
2. **Backend**: `pip install -r requirements.txt`, then run `uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`
   under systemd (or use `backend/Dockerfile`, which is untested). Put Caddy or nginx in front for TLS.
3. **Frontend**: `cd frontend && npm ci && npm run build`; serve `frontend/dist` as static files. Easiest is the same domain:
   route `/api/*` to the backend and everything else to `dist` (with a fallback to `index.html` for client-side routes).
   For a separate API domain set `VITE_API_BASE_URL=https://api.your-domain` **at build time** and add the site origin to `CORS_ORIGINS`.
   Allow request bodies of at least 60 MB (`client_max_body_size 60m;` in nginx) for audio uploads.
4. **Before going public** (not implemented yet): database migrations (Alembic; tables are currently created on startup),
   login rate limiting, email verification and password reset, backups of the database and `storage/`, log shipping,
   a privacy policy and community consent process, a scheduler if time capsules are ever to be released automatically.
5. **Verify**: `/api/health`, `/api/config/status`, then `python scripts/verify_bodhan.py` on the server.
