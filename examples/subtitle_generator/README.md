# Subtitle generator

> Upload a video or paste a link to one (a direct video link or a YouTube video), get timed subtitles (SRT) in its spoken language and, optionally, translated into another, for creators and educators publishing in Indian languages.

**Bodhan models:** indic-transcribe, indic-translate
**Languages shown:** Hindi, English, Tamil, Telugu, Malayalam, Bengali, Marathi, Gujarati, Kannada, Punjabi, Odia, Urdu, Assamese, Nepali, Sanskrit, Bhojpuri, Maithili (spoken); the same minus Bhojpuri (translation)
**Format:** FastAPI backend + React (Vite) web app

## What it does

Regional-language videos rarely ship with subtitles because writing and timing them by hand is slow. This recipe takes a video, from an uploaded file, a direct link to a video file, or a YouTube link (the two kinds of link are optional, see [Video links](#video-links-optional)), transcribes its speech with `indic-transcribe`, optionally translates every subtitle line with `indic-translate`, and returns standard `.srt` files. The web app plays the video with its subtitles, lets you click any line to jump to that moment, correct any line, and download the SRT files. Past jobs stay in a history you can open again.

## Setup

You need **Python 3.10+** and **Node.js 20.19+ or 22.12+**. FFmpeg and Deno come with `pip install` (inside `backend/venv`), so nothing else needs installing.

```bash
cd examples/subtitle_generator

cp .env.example .env   # then paste your keys from https://console.bodhan.ai (one per model)

# Backend
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd ..

# Web app (installs into frontend/node_modules)
npm install
```

No key yet? Set `DEV_MOCK_MODE=true` in `.env` to try the whole app with canned transcripts and translations.

## Run

Two terminals:

```bash
cd backend   # with venv activated
fastapi dev app/main.py   # reloads when you edit code; serves on port 8000
```

```bash
npm run dev
```

Open http://localhost:5173, drop in a video (or switch to **Paste a link**), pick its spoken language (and a translation language if you want one), and click **Generate Subtitles**. The web app forwards `/api` requests to the backend on port 8000, so there is nothing else to configure. http://localhost:8000/api/health shows whether FFmpeg and your keys were found.

### Or with one command

After the setup above, from the project root:

```bash
npm start
```

This starts the backend (port 8000) and the web app (port 5173) together in one terminal, each line labelled, and opens the app in your browser once both are ready (`npm start -- --no-open` skips that); Ctrl+C stops both. It runs the backend with `fastapi run`, so editing code doesn't restart it in the middle of a job; use the two terminals above while developing.

### Or with Docker (optional)

An extra way to run it; the setup above remains the main one. No Python or Node setup needed, only Docker and `.env` (copy `.env.example` and add your keys):

```bash
docker compose up --build
```

Open http://localhost:8000: the image builds the web app and the backend serves it on the same port. The first build takes a few minutes. The keys are read from `.env` when the container starts, never copied into the image; the job history is kept in a Docker volume (`subtitle-data`), separate from the normal setup's; and the app is reachable only from this computer. The container starts again with Docker Desktop (`restart: unless-stopped`) and holds port 8000 until you run `docker compose stop`.

### Video links (optional)

Besides uploading a file, a job can start from a pasted link. There are two kinds of link, each with its own switch in `.env` (both `false` in `.env.example`); restart the backend after changing them. When either is on, the page shows an **Upload a file / Paste a link** switch, and the hint under the link box says which kinds work.

| Setting | Allows | How it works |
| --- | --- | --- |
| `DIRECT_VIDEO_LINKS=true` | A **direct video link**: a link that returns the video file itself, such as `https://example.com/talk.mp4` or a public cloud-storage *file* link | The backend downloads the file (same 100 MB and 15-minute limits as an upload) and the page plays it from the link. The server must send a video type, or a file name ending in .mp4, .mov, .mkv, .webm, .avi or .m4v |
| `YOUTUBE_LINKS=true` | A **YouTube link** to one video (`watch?v=…`, `youtu.be/…`, Shorts) | Only the audio is downloaded, with [yt-dlp](https://github.com/yt-dlp/yt-dlp). The page asks for **Allow YouTube links** on each visit, with a reminder that YouTube's terms apply: use only videos you have the right to use. The video plays in YouTube's own player, with the current line in a strip under it |

Not supported: share links that open a web page instead of the file (a Google Drive or Dropbox *share* page, for example), links to other video sites (Vimeo, Instagram…), playlists and channels, and links to this computer or a private network, which are refused so a pasted link can't reach local services.

YouTube needs a JavaScript runtime (Deno), which `pip install -r requirements.txt` already installs. Private, members-only, age-restricted and live videos aren't supported, and the 15-minute limit is checked before downloading. YouTube changes often: **if YouTube links stop working, update yt-dlp** (`pip install -U yt-dlp` with `venv` activated), and tell the maintainers so the pinned version can be raised.

## How it works

1. The video comes from an upload, a direct video link or a YouTube link ([Video links](#video-links-optional)); for a link, the backend downloads it first (for YouTube, the audio only). The backend checks the video (type, 100 MB, 15 minutes, has an audio track), records a job in its database and answers at once; the rest runs in the background while the page shows each step and a progress bar. The backend extracts the audio as 16 kHz mono WAV with FFmpeg.
2. Every stretch of the audio with sound in it is sent: speech, singing, speech over music. Only real quiet (more than 35 dB below the track's loud parts) is skipped, since silence is billed too. The [Silero](https://github.com/snakers4/silero-vad) voice activity detector (`silero-vad-lite`, runs locally) finds where people speak; it is used to choose where to cut and where to place lines, never to drop audio. The audio is cut into pieces at the pauses (a new piece at each clear pause once a piece holds 6 seconds, never more than 24 seconds, since `indic-transcribe` accepts about 30). Where sound runs on longer than that (music, a song), the cut goes at the quietest moment in the last 6 seconds before the limit, outside speech where possible.
3. Each piece goes to `POST /v1/audio/transcriptions` with `indic-transcribe` and the chosen language.
4. Each transcript is split into short subtitle lines, which are placed on the measured stretches of speech in that piece, longer lines getting more time. Pauses get no subtitle.
5. Translation is optional and off by default (the page remembers your last choice). If it is turned on, each line goes to `indic-translate` (`POST /translate`, or chat completions while that route returns 404); translated lines keep the original timings.
6. The subtitle lines are saved with the job, and the browser shows them with SRT downloads. The video and audio are deleted; only the text is kept, so past jobs can be opened again from **History** (link jobs play their video again; for an upload, choose the same file to preview it).

In the page:

- **Correct a line:** the pencil button (Enter saves, Esc cancels). The timing is kept and the downloads include the fix.
- **Follow the video:** the line being spoken is highlighted and kept in view. Scroll the list yourself and it stops following, so you can read ahead; **Back to current line**, or clicking a line, follows again.
- **Player:** play, ±5 s, volume, a speed menu (0.5x to 2x), subtitles on or off, fullscreen. Keys: Space, ←/→, M, C, F, and T for the transcript.
- **Fullscreen:** the transcript on the right (click to jump, pencil to correct, `T` to hide), and a language menu to switch between the original and the translated subtitles.
- **If a job stops** (the backend was stopped, or a Bodhan error): its subtitles are kept once transcription is done, and each translated line as it is ready. **Resume translation** finishes only the missing lines; a job that stopped earlier needs the video again, and **Run again** fills in its settings (a link job starts again from its link).

| File | What it does |
|---|---|
| [`backend/app/jobs.py`](backend/app/jobs.py) | The steps above, in order: start reading here |
| [`backend/app/bodhan.py`](backend/app/bodhan.py) | The two Bodhan API calls, with retry on rate limits |
| [`backend/app/subtitles.py`](backend/app/subtitles.py) | Transcript → timed lines → SRT |
| [`backend/app/audio.py`](backend/app/audio.py) | FFmpeg: inspect the video, extract and cut audio; group speech into pieces |
| [`backend/app/vad.py`](backend/app/vad.py) | What to send (every stretch of sound, by loudness) and where people speak (Silero voice activity detection) |
| [`backend/app/main.py`](backend/app/main.py) | HTTP API: `GET /api/health`, `POST /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs`, `POST /api/jobs/url`, `POST /api/jobs/{id}/resume`, `PATCH /api/jobs/{id}/lines` |
| [`backend/app/download.py`](backend/app/download.py) | Direct video links: checks the link, downloads the file |
| [`backend/app/youtube.py`](backend/app/youtube.py) | YouTube links: reads the video id, checks length and live status, downloads the audio with yt-dlp |
| [`backend/app/db.py`](backend/app/db.py) | The `jobs` table (SQLModel): SQLite file `backend/data/subtitles.db` by default, or any database set in `DATABASE_URL` |
| [`backend/app/config.py`](backend/app/config.py) | Settings from `.env` |
| [`frontend/src/App.jsx`](frontend/src/App.jsx) | The page; components in `frontend/src/components/` |

The API keys stay on the backend; the browser only talks to it. The design is written up in
[`docs/SOFTWARE_SPECIFICATION.md`](docs/SOFTWARE_SPECIFICATION.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Tests need no key or network: Bodhan, YouTube and video links are replaced by fakes (run from `backend/` with `venv` activated):

```bash
python -m unittest
```

### Code quality

- **Backend:** [Ruff](https://docs.astral.sh/ruff/) lints and formats, with the same version and rules as the cookbook repo (`backend/pyproject.toml`). Install it with `pip install -r requirements-dev.txt`, then from `backend/`: `ruff check .` and `ruff format .`
- **Web app:** [ESLint](https://eslint.org/) with React, React Hooks and accessibility rules (`frontend/eslint.config.js`), and [Prettier](https://prettier.io/) for formatting (`frontend/.prettierrc.json`). From the project root: `npm run lint` and `npm run format`
- **Types:** `pyrightconfig.json` points Pyright and VS Code (Pylance) at `backend/venv`; `npx pyright` from the project root reports no errors.

## Limitations

- **Timing is approximate.** `indic-transcribe` returns no timestamps, so lines are placed on the measured speech by length; in songs and over music the timing is rough.
- **Choose the spoken language.** `indic-transcribe` doesn't detect it; a wrong language gives text in that language's script.
- **Limits:** videos up to 15 minutes and 100 MB; one job at a time. Music and singing are sent and billed; silence is not.
- **Long videos** make one translation call per line and can hit rate limits (retried automatically).
- **Links:** of video sites, only YouTube is supported. Direct links depend on the host: some refuse downloads or expire.

## Ideas for contributors

- Correct line timings in the page, not only the text
- Word-level timing: forced alignment, or Bodhan timestamps once the API returns them
- Translate whole pieces instead of single lines, for better context and far fewer calls
- Delete jobs from the history
- Burned-in (hard-coded) subtitles
- Play uploaded videos again from the history
- More video sites, one at a time
- Several jobs at once
