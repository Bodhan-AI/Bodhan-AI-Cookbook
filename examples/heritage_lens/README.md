# HeritageLens

*Every story deserves to live beyond its generation.* A full-stack platform for preserving oral histories,
documents and traditions in their own languages, using five Bodhan AI services (`indic-transcribe`,
`indic-translate`, `indic-transliterate`, `indic-ocr`, `indic-speak`) called **only from the backend**.

```
heritagelens/
  backend/    FastAPI + SQLAlchemy + JWT + private file storage + Bodhan client   (see backend/README.md, docs/)
  frontend/   React + TypeScript + Vite + Tailwind + Router + Framer Motion + Recharts + Leaflet
  setup-env.ps1   private, hidden-input helper that writes your Bodhan keys to backend\.env
```

## Setup: 1. Start the backend (Windows PowerShell)
```powershell
winget install Gyan.FFmpeg                    # once; then open a NEW terminal. Check: ffmpeg -version
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1                  # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements-dev.txt
cd ..
.\setup-env.ps1                               # creates backend\.env, generates SECRET_KEY, asks for your keys (hidden)
cd backend
uvicorn app.main:create_app --factory --reload
```
Check http://127.0.0.1:8000/api/health and http://127.0.0.1:8000/api/config/status.

## Run: 2. Start the frontend (second terminal)
```powershell
cd frontend
npm install
npm run dev                                   # http://localhost:5173  (Vite forwards /api to port 8000)
```

## 3. Where to add your Bodhan keys
Only in **`backend/.env`** (git-ignored). Easiest: run `.\setup-env.ps1`; input is hidden and nothing is echoed.
Or edit the file in Notepad: either set `BODHAN_API_KEY` (one key for everything) or the five
`BODHAN_<SERVICE>_API_KEY` lines. Values starting with `your_` count as "not set". Restart the backend afterwards.
Never put keys in the frontend, in chat, or in Git. **Keys pasted into a chat must be rotated.**

## 4. Test the five Bodhan services
```powershell
cd backend
python scripts/verify_bodhan.py               # PASS / FAIL / SKIPPED per service; keys never printed
python scripts/verify_bodhan.py --only translate,transliterate --json report.json
```
It tests: speak (a Telugu greeting, real WAV), transcribe (re-using that audio or `--audio file`), translate,
transliterate, and ocr (a generated image, or `--ocr-image file`). A service is "live verified" only when this shows PASS.
In the app: **Try it** page (Live Mode), Script Explorer, Digitizer, Audio Library, and the Preserve wizard.
`/api/config/status` shows which keys are present, not whether Bodhan accepts them.

## 4b. Demo Mode vs Live Mode
Header switch, always visible with a banner. **Demo**: labelled sample records (Bathukamma, Gond art, Japanese craft),
hand-written ILLUSTRATIVE outputs, **no Bodhan calls, no credits**. **Live**: your real database and real Bodhan calls
through the server; unconfigured or failing services show explicit errors. The app never switches modes by itself.
Every record carries provenance: `demo`, `live_bodhan` or `user_authored`.

## 5. Register and preserve your first story
1. Open http://localhost:5173 → **Sign in** → **Create account** (no email verification exists yet).
2. Switch the header to **Live** (needed for transcription, translation, narration).
3. **Preserve a Story**: fill in the details → *Save and start* (a draft now exists).
4. **Record** (allow the microphone) or upload audio → the original is stored untouched.
5. **Transcribe**: pick the language (Telugu = `te`), run, correct the text, *Use as story text*.
6. **Translate & narrate**: translate, transliterate to Roman, generate speech; each is a separate version.
7. Add photographs/documents → **Review**: choose privacy, tick consent, *Publish* (or *Save draft*).

## 6. Run the tests
```powershell
cd backend ; pytest -v                         # needs requirements-dev.txt
cd ..\frontend ; npm test
```
Status of these suites is honest and listed in `backend/docs/TESTING.md` and `docs/QUALITY_REPORT.md`.

## 7. Deploy
See `docs/DEPLOYMENT.md`.
