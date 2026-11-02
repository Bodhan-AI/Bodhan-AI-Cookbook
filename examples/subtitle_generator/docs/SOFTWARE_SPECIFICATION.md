# Subtitle Generator — Software Specification

> Software specification for the Bodhan AI Cookbook recipe `examples/subtitle_generator`. The "Built in V0"
> column in Section 6 records what the code does today; gaps are listed in Sections 6, 10 and 14. The
> companion design is in [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Document details

| Field | Value |
| --- | --- |
| App name | Subtitle Generator (folder `examples/subtitle_generator`) |
| One-line description | Upload a video or paste a link to one (a direct video link or a YouTube video) and get timed subtitles (SRT) in its spoken language and, optionally, translated into another |
| Authors | Sumit Bhukal |
| Bodhan point of contact | @Bodhan-AI/cookbook-maintainers |
| Recipe proposal issue | To be opened with the code PR |
| Status | Draft for review |
| Version | 0.1 |
| Last updated | 2 Nov 2026 |
| Reviewers | Filled in at the review meeting |
| Architecture document | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Designs | None; the built screen is described in Section 11 |

### Change history

| Version | Date | Author | What changed |
| --- | --- | --- | --- |
| 0.1 | 30 Oct 2026 | Sumit Bhukal | First version, written against the working prototype (not yet published) |

## 2. Summary

- **What it is:** A web app that turns a video (an upload, or optionally a YouTube or direct video link) into subtitle files: one in the language spoken in the video, and optionally one translated into another language with the same timings.
- **Who it is for:** Creators and educators who publish video in Indian languages and want subtitles without writing and timing them by hand.
- **Bodhan models used:** `indic-transcribe`, `indic-translate`.
- **Platform:** Web app (React) with a Python backend (FastAPI), run locally from the cookbook folder.
- **What V0 proves:** A creator can upload a 15-minute Hindi video and, in a few minutes, download Hindi and English SRT files whose lines appear on screen when they are spoken.

## 3. Problem and users

**Problem statement.** Most videos in Indian languages are published without subtitles, because
writing and timing them by hand takes several times the length of the video, and translating
them needs a second language skill. That leaves out viewers who are deaf or hard of hearing,
viewers who speak another language, and anyone watching with the sound off. Creators need
subtitle files they can upload straight to YouTube or a course platform, in the spoken language
and in at least one other.

### Users

| Persona | Who they are | Languages and script | Device and connectivity | What they need from the app |
| --- | --- | --- | --- | --- |
| Creator (primary) | Regional YouTuber, teacher or small media team publishing a few videos a week | Speaks Hindi, Tamil, Bengali or another Indian language; reads the native script | Laptop or desktop browser, broadband good enough to upload up to 100 MB | Subtitle files in the spoken language, and one translation, ready to upload |
| Viewer (indirect) | Someone watching the creator's video | Any; may not know the spoken language | Any device | Subtitles that appear when the words are spoken, short enough to read |
| Developer (secondary) | Someone building their own captioning tool on Bodhan | Reads Python and JavaScript | Development machine | A clear, small example of chunking audio for `indic-transcribe` and timing subtitles without timestamps |

### Context to address

- **Language and script:** 17 spoken languages offered (Hindi, English, Tamil, Telugu, Malayalam, Bengali, Marathi, Gujarati, Kannada, Punjabi, Odia, Urdu, Assamese, Nepali, Sanskrit, Bhojpuri, Maithili). Translation into the same list except Bhojpuri, which `indic-translate` does not support. Subtitles are written in the native script.
- **Literacy and reading level:** Subtitle lines are kept short (at most about 8 words or 42 characters) so viewers can read them in time.
- **Devices:** Any modern desktop browser. The creator runs the app on their own computer.
- **Connectivity:** The video is uploaded to the local backend; only audio pieces and text go to Bodhan.
- **Age:** The creator is an adult. Videos may show other people, including children; see Section 10.

### Existing alternatives

| Alternative | What it does well | Where it falls short for these users |
| --- | --- | --- |
| Automatic captions on video platforms | Free and built in | Captions stay inside that platform; no subtitle file in another language to use elsewhere |
| Desktop subtitle editors (for example Subtitle Edit, Aegisub) | Precise timing, full control | Every line is typed and timed by hand; no transcription or translation |
| Paid human subtitling | Accurate, idiomatic translation | Cost and turnaround are out of reach for small creators |

## 4. Goals, non-goals and V0 scope

### Goals

1. G1: A creator gets a usable SRT file in the spoken language for a video of up to 15 minutes, with no manual timing.
2. G2: The same run can produce a translated SRT whose lines keep the original timings.
3. G3: Developers can read the code to learn how to send long audio to `indic-transcribe` (which accepts about 30 seconds per request) and how to time subtitles when the API returns no timestamps.

### In V0

- Upload a video by drag-and-drop or file picker; MP4, MOV, MKV, WEBM, AVI or M4V; up to 100 MB and 15 minutes (G1)
- Choose the spoken language, and optionally a translation language (G1, G2)
- Cut the audio at pauses into pieces of at most 24 seconds and transcribe each piece (G1, G3)
- Split transcripts into short lines placed on the measured stretches of speech (G1, G3)
- Translate every line, keeping its timing (G2)
- Preview the video with the current line drawn over it; click a line to jump there (G1)
- Correct the text of any line before downloading (G1)
- Download or copy the original and translated SRT files (G1, G2)
- Background jobs with progress, a job history, and resuming a translation that stopped (G1, G2)
- Optional: start from a YouTube link or a direct link to a video file, each behind its own switch (G1)
- A mock mode that runs without a Bodhan key, for trying the app and for tests (G3)

### Deferred to V1 or later

- Editing subtitle timing in the app (text can be corrected already)
- Word-level timing, from Bodhan timestamps when available, or forced alignment
- Several translation languages in one run
- Burned-in (hard-coded) subtitles
- Batch processing of a video library
- A hosted deployment with sign-in and per-user limits
- Links to other video sites (only YouTube for now, FR-20)
- Several jobs at once (V2)
- Playing uploaded videos again from the history (V2 idea: a copy kept in the browser, so nothing new is stored on the server)

### Non-goals

- Not live captioning of streams or meetings.
- Not a full subtitle editor; files can be refined in existing editors after download.
- No speaker labels: `indic-transcribe` does not identify speakers.
- No storage of videos or audio: they are deleted when a job ends. Only subtitle text is kept, for the job history.

## 5. Platform and form factor

| Question | Answer |
| --- | --- |
| Primary platform | Web app in a desktop browser, with a local Python backend |
| Why this platform for these users | Creators prepare videos on a computer, and SRT files go straight from the browser to the upload page of their video platform |
| Supported targets | Current Chrome, Edge and Firefox; Python 3.10+ and Node.js 20.19+ to run it |
| Backend needed? | Yes. The Bodhan key must stay out of the browser (cookbook rule), and FFmpeg must process the audio |
| How reviewers will run V0 | Follow the README: install the backend (`pip install -r requirements.txt`) and the web app (`npm install`), then start both. `DEV_MOCK_MODE=true` runs it without a key |
| Offline behaviour | None for real subtitles; mock mode works offline |
| Where the code lives | `examples/subtitle_generator/` in the Bodhan AI Cookbook |

## 6. User journeys and functional requirements

### Core journeys

J1 — A creator subtitles a Hindi video:

1. The creator drops a video into the page and leaves the spoken language as Hindi.
2. They click Generate Subtitles and wait, with the current step and a progress bar on screen.
3. The video appears with subtitles drawn over it, and the subtitle lines are listed beside it.
4. They play it through, click a line to check a moment, and download the Hindi SRT.

J2 — The same creator also wants English subtitles: they turn on Translate subtitles and pick English before generating, then download both SRT files.

J3 — A developer without a key sets `DEV_MOCK_MODE=true` and runs J1 with canned text to see the whole flow.

J4 — A creator whose video is already on YouTube pastes its link instead of uploading, turns on **Allow YouTube links**, and gets the same subtitles while the video plays in YouTube's player (only when `YOUTUBE_LINKS=true`).

### Functional requirements

The last column records whether the prototype meets each requirement today.

| ID | The app shall… | Journey | Priority | Acceptance criterion | Built in V0 |
| --- | --- | --- | --- | --- | --- |
| FR-1 | Accept one video upload of an allowed type, up to 100 MB | J1 | Must | A 50 MB MP4 is accepted; a `.txt` file and a 120 MB file are refused with a message | Yes |
| FR-2 | Refuse videos it cannot subtitle, with a plain message | J1 | Must | An empty file, a video with no audio track and a video over 15 minutes each get their own message | Yes |
| FR-3 | Cut the audio into pieces of at most 24 seconds, at pauses where possible | J1 | Must | No request to `indic-transcribe` carries more than 30 s of audio; silence between sentences is not sent | Yes |
| FR-4 | Transcribe every piece in the chosen language | J1 | Must | A clear Hindi clip produces Hindi text in Devanagari | Yes |
| FR-5 | Split transcripts into readable lines and place them on the measured speech | J1 | Must | Lines have at most about 8 words; no line is shown during a pause; on the test clip in Section 12, lines start within 0.5 s of the speech | Yes (0.2 s on the test clip) |
| FR-6 | Translate every line into the chosen language, keeping its start and end times | J2 | Must | The translated SRT has the same number of cues with identical timestamps | Yes |
| FR-7 | Preview the video with the current line over it, and seek when a line is clicked | J1 | Must | Clicking a line moves playback to its start and shows that line | Yes |
| FR-8 | Download and copy valid SRT files | J1, J2 | Must | The files open in a standard player and on YouTube's subtitle upload | Yes (format tested; YouTube upload not yet tried) |
| FR-9 | Show a plain-language message when any step fails | J1 | Must | A missing key, a rate limit and an unreachable backend each give a different, readable message | Yes |
| FR-10 | Delete the uploaded video and all audio files when a job ends, whether it succeeds or fails | J1 | Must | The temporary folder is gone when the job ends | Yes |
| FR-11 | Show progress while a long video is processed | J1 | Should | The user sees which step is running and roughly how far along it is | Yes: step and progress bar |
| FR-12 | Run without a Bodhan key in mock mode | J3 | Should | With `DEV_MOCK_MODE=true` and no key, J1 completes with canned text | Yes |
| FR-13 | Remove obviously broken transcript text before it becomes subtitles | J1 | Should | A transcript ending in one character or a short group repeated 100+ times produces no junk line | Yes |
| FR-14 | Keep a history of jobs and their subtitles | J1 | Should | After a page reload, a finished job can be opened again and its SRT files downloaded | Yes |
| FR-15 | Resume a job that stopped during translation without uploading again | J2 | Should | After the backend is stopped mid-translation, the original subtitles are shown and Resume translates only the missing lines | Yes |
| FR-16 | Make translation optional | J1 | Must | With translation off (the default), no `indic-translate` call is made and only the original SRT is offered | Yes |
| FR-17 | Correct the text of any subtitle line | J1 | Should | An edited line keeps its timing, is in the downloaded SRT, and is still there after a page reload | Yes |
| FR-18 | Show the transcript next to the video in fullscreen, and let the viewer hide it | J1 | Should | In fullscreen the lines are listed on the right with the current one highlighted; lines can be corrected there as in the list; one button or `T` hides and shows them | Yes |
| FR-19 | Start a job from a direct link to a video file | J1 | Should | A link that returns a video file works like an upload; page links and private addresses are refused with a message | Yes; off by default (`DIRECT_VIDEO_LINKS`) |
| FR-20 | Start a job from a YouTube video link | J4 | Should | One public video of up to 15 minutes gives the same subtitles as uploading it; playlists, live, private and longer videos are refused before downloading | Yes; off by default (`YOUTUBE_LINKS`) |
| FR-21 | Let the viewer read other lines while the video plays | J1 | Should | Scrolling the lines stops them following the video; **Back to current line** follows again | Yes |
| FR-22 | Choose the playback speed and, in fullscreen, the subtitle language | J1 | Could | Both menus work with the mouse and the keyboard | Yes |

## 7. Bodhan API usage

### Usage

| Model | Used for | What the app sends | What it uses from the response | Languages | Calls | Acceptable wait |
| --- | --- | --- | --- | --- | --- | --- |
| `indic-transcribe` | FR-4 | `POST /v1/audio/transcriptions`, multipart: a 16 kHz mono 16-bit WAV piece of at most 24 s, `model`, `language` | `text` | 17 listed in Section 3 | About one per 6–24 s of speech (3 for 18.6 s of speech in testing) | Measured: about 1 s per piece |
| `indic-translate` | FR-6 | `POST /translate` with `text`, `target_language`, `script=native`. That route returned 404 on the live API, so after the first 404 the app sends `POST /v1/chat/completions` with `model`, one user message and `target_language_code` | `translation`, or `choices[0].message.content` | Same list without Bhojpuri | One per subtitle line (9 for 18.6 s of speech in testing) | Measured: 4 s for 9 lines, two at a time |

### Design questions answered

- **Over-limit input:** The backend never sends more than 24 s of audio in one request (the API accepts about 30 s). Videos over 15 minutes or 100 MB are refused before any call.
- **Audio format:** FFmpeg converts any accepted video to 16 kHz mono 16-bit WAV, the documented format, so the containers `indic-transcribe` refuses never reach it.
- **Language selection:** The creator picks the spoken language, and it is always passed to `indic-transcribe`. Auto-detection is not used.
- **Timing without timestamps:** `indic-transcribe` returns text only. The Silero voice activity detector (run locally) measures where speech starts and stops, and loudness where any sound is; lines are placed on those stretches, longer lines getting more time.
- **Failure behaviour:** Rate limits (429) are retried with the wait the API asks for, up to five attempts. Any other failure stops the job and shows a message (FR-9).

**Companion services:** No other AI service. Optionally (`YOUTUBE_LINKS=true`), YouTube: the backend downloads the video's audio with yt-dlp, and the page embeds YouTube's player.

### Gaps and feedback for Bodhan

- **No timestamps from `indic-transcribe`.** Subtitles need them. `response_format=verbose_json` is accepted today and returns the OpenAI shape, but `words` and `segments` are empty; `timestamp_granularities[]` is rejected with 422. The app already uses `words` and `segments` when present (`BODHAN_TIMESTAMPS=true`), so filling them would give word-accurate subtitles with no app change.
- **`POST /translate` returned 404** on the live API, although the API reference lists it as the main route. The chat-completions route works.
- **Repeated-character output.** `indic-transcribe` sometimes appends one character (seen: U+0B44) or a short group repeated 100+ times, even on the cookbook's own clean `sample_hindi.wav`. The app strips it.
- **Unpunctuated transcripts.** Text comes back without sentence punctuation, so sentence boundaries cannot be read from the text.
- **Keys per model** mean a new user needs two keys before the first run.

## 8. Non-functional requirements

| Area | Requirement | How it will be checked |
| --- | --- | --- |
| Latency | A 10-minute video with translation is ready within 5 minutes | Not met yet: a 12-minute video with translation (from a YouTube link, 155 lines) took about 11 minutes, including the download; translation sends one request per line (R3, Q4) |
| Timing accuracy | 90% of lines start within 0.5 s of the speech | Timing benchmark planned (Section 12); 100% on the one test clip so far |
| Readability | Lines need at most 20 characters per second to read, and stay on screen at least 0.7 s | Checked on every output by the planned evaluation script; the test clip peaked at 12.2 |
| Reliability | One failed call must not lose the rest of the job | Partly met: a stop during translation keeps the subtitles and resumes only the missing lines; a failure during transcription means running the job again (Section 14) |
| Upload size | At most 100 MB and 15 minutes per video | Enforced by the backend |
| Privacy | No video or audio kept after a job; subtitle text kept only in the local database; no video, audio or text in logs | Met: logs hold counts, statuses and timings only |
| Accessibility | Every control reachable by keyboard and labelled for screen readers; light and dark themes | Manual check; player has keyboard shortcuts |
| Portability | Runs on Windows, macOS and Linux with only Python and Node.js installed | FFmpeg and Deno come from pip; tested on Windows, and on Linux in the optional Docker image |
| Quality of AI output | A native speaker accepts at least 8 of 10 subtitle lines as correct, per language tested | Not yet done |

## 9. Cost and usage estimate

Prices are from [Bodhan pricing](https://console.bodhan.ai/#pricing) on 16 Oct 2026: `indic-transcribe` ₹0.10 per minute of input audio; `indic-translate` ₹0.20 per 10,000 output tokens. Usage figures are estimates, scaled from the test run.

### Cost per video

Assumes a 10-minute video with 8 minutes of speech, about 30 subtitle lines per minute of speech, and about 10 output tokens per translated line.

| Model | Priced per | Units per video | Price per unit (₹) | Cost (₹) |
| --- | --- | --- | --- | --- |
| `indic-transcribe` | minute of input audio | 8 (silence between sentences is not sent) | 0.10 | 0.80 |
| `indic-translate` | 10,000 output tokens | 0.24 (240 lines × 10 tokens) | 0.20 | 0.05 |
| **Total with translation** | | | | **0.85** |

Transcription is almost all of the cost. Cutting at pauses helps directly, because silence is never sent or billed.

### Volume and credits

| Estimate | Number |
| --- | --- |
| Cost per 10-minute video (₹) | 0.85 |
| Videos per active creator per month | 12 (three a week) |
| Cost per active creator per month (₹) | About 10 |
| Credits needed to build and test V0 (₹) | 30: about 35 test videos of a few minutes each, plus failed runs |
| Credits for a 10-creator pilot (₹) | 150 |
| Peak requests per minute, per key | Transcription: up to about 25 (one piece every ~2.4 s). Translation: about 2 at a time, up to roughly 130 a minute on long videos, which is what hits rate limits today |
| Who pays after V0 | Whoever runs the app, with their own Bodhan key (Section 10) |

## 10. Security, privacy and data handling

**API key custody: on the backend.** The key is read from `.env` by the Python backend, which is
the only part that calls Bodhan. The browser talks to the backend through the web app's dev
server and never sees the key. `.env` is git-ignored; `.env.example` holds placeholders. This
follows the cookbook rule for browser recipes.

Models used: `indic-transcribe` and `indic-translate`. Bodhan keys work with one model each, so there is one key per model (`BODHAN_TRANSCRIBE_API_KEY`, `BODHAN_TRANSLATE_API_KEY`); `BODHAN_API_KEY`, a key for every model, fills in for an empty one.

### Data inventory

| Data item | Collected from | Sent to | Stored where, and for how long | Personal data? |
| --- | --- | --- | --- | --- |
| Uploaded video | The creator | The local backend only | A temporary folder, deleted as soon as transcription ends (or the job fails) | Possibly: faces and voices of people in the video |
| Pasted link, and the video or audio it points to | The creator; YouTube or the linked site | The backend downloads it; YouTube also learns the video was requested | The link: the local database, with the job. The download: the temporary folder, deleted with it | Possibly |
| Extracted audio and audio pieces | The video, by FFmpeg | Bodhan `indic-transcribe` (pieces with speech only) | The same temporary folder, deleted with it | Yes: voices |
| Transcript and subtitle lines | Bodhan responses | Bodhan `indic-translate`; the creator's browser | The local database (`jobs` table) until the creator deletes it; browser memory while shown | Possibly, depending on what is said |
| Job details (file name, languages, status, times) | The creator's upload | Nowhere | The local database, with the subtitle lines | Possibly: a file name can name a person |
| SRT files | The browser | The creator's own disk, when downloaded | Wherever the creator saves them | Possibly |
| Theme and form choices (languages, translation on or off, upload or link) | The creator | Nowhere | Browser `localStorage` | No |
| Bodhan keys | The person running the app | Bodhan, as a bearer token | `.env` on that person's machine | Credentials, not personal data |

### Questions answered

- [x] **Personal data collected:** voices and speech of people in the uploaded video. Video and audio are deleted when the job ends; the transcript and translation are kept in the local database.
- [ ] **Consent of people in the video:** the creator is responsible for having the right to subtitle their own video. Open question Q3: should the app say so before upload?
- [x] **Users under 18:** the app targets adult creators. Videos may contain children; no video or audio is stored, and demos use synthetic or consented clips only.
- [ ] **Consent and deletion:** subtitle text stays in `backend/data/subtitles.db` until that file is deleted. A delete button per job is not built yet.
- [x] **Data leaving the machine:** only audio pieces and text, only to Bodhan (`api.bodhan.ai`). No analytics. Fonts load from Google Fonts. With YouTube links on, the backend fetches the video's audio from YouTube and the page embeds YouTube's player (privacy-enhanced mode, youtube-nocookie.com).
- [x] **Sample data:** none committed. Tests use synthetic tones, and the cookbook's shared `sample_data/sample_hindi.wav` when present.
- [ ] **Backend abuse:** V0 runs locally for one person. If it were hosted as is, anyone could use it to spend the owner's credit (no sign-in, no limits); V1 must add both before hosting.

## 11. UX and design

**Mock-ups:** none. The built screen is the reference.

| Screen | Purpose | Key elements | Loading, empty and error states |
| --- | --- | --- | --- |
| Main page (single screen) | Upload or paste a link, generate, preview and download | Left: file or link, spoken language, translation, Generate. Right: the player with the current line, its controls and fullscreen transcript; Original/Translated tabs; the line list with editing; downloads. History below | Empty: "No video yet". Loading: the step and a progress bar. Error: a red banner with the message. Success: a green banner, and the line being spoken is highlighted |

- **Interface language:** English. Subtitle content is in any of the supported languages.
- **Approximate timing** is labelled next to the subtitle tabs, with a tooltip explaining why.
- **Look:** Bodhan's design, so cookbook apps look alike: light mode follows bodhan.ai, dark mode console.bodhan.ai. The tokens are in `frontend/src/theme.css`, section 1, ready to copy into other recipes. Known gap from Bodhan's own colours: white text on the orange buttons is about 3:1, below the 4.5:1 guideline.
- **Credit to Bodhan:** the page names `indic-transcribe`, `indic-translate` and the Bodhan AI Cookbook, and the header shows the Bodhan mark.

## 12. Success criteria and demo plan

| Criterion | Target | How it is measured |
| --- | --- | --- |
| J1 and J2 work end to end with live Bodhan calls | 9 of 10 runs | Scripted runs on 3–5 test videos of 1–10 minutes |
| Lines appear when spoken | 90% start within 0.5 s | Timing benchmark: real speech clips joined with known pauses, so the true times are known |
| Subtitles are readable | At most 20 characters per second; at least 0.7 s on screen | Evaluation script on every output |
| Transcription is understandable | 8 of 10 lines accepted, per language | Rated by a native speaker (Hindi first) |
| Time to subtitles | 10-minute video with translation in under 5 minutes | Timed on the test videos |

### Demo

A one-minute recording, linked in the PR: upload a short narrated sample video made for the demo, generate
subtitles with a translation, play it with the synced lines, use the fullscreen transcript, download the SRT
files and open the job from History. For a live demo, the same steps take about 3 minutes.

## 13. Milestones and timeline

| Milestone | Date | Done when |
| --- | --- | --- |
| Idea in the cookbook application list | 7 Oct 2026 | Listed as application 10 |
| Working prototype | 14 Oct 2026 | J1 and J2 run against the live API |
| Pause-based subtitle timing | 15 Oct 2026 | FR-5 met on the test clip |
| Spec and architecture documents | 16 Oct 2026 | Ready for team review |
| Job history, editing, links and YouTube, player menus | 30 Oct 2026 | FR-14 to FR-22 met |
| Peer review | To be set | Comments resolved in these documents |
| Code PR to the cookbook | Nov 2026 | Passes the cookbook validator; README, tests, `.env.example` and the demo video included |
| V1: evaluation | To be set | Timing benchmark results published |

**Team and time:** one developer, alongside primary work.

## 14. Risks, dependencies and open questions

### Risks

| ID | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Lines within a long stretch of speech drift, because placement within a stretch is estimated | Medium | Medium | Pause-based pieces keep stretches short; word timestamps from Bodhan or forced alignment in V1 |
| R2 | Words are lost because a detector decides they aren't speech (singing, quiet speech) | Low | High | Everything with sound is sent; the speech detector only sets cut points and timing. Found when a song gave 3 lines; now 28 |
| R3 | Long videos hit translation rate limits, slowing jobs | High | Medium | Retries already wait as asked; translate whole pieces instead of single lines in V1 (also better quality) |
| R4 | One failed call loses the whole job | Medium | Medium | V1: retry 5xx errors and keep finished pieces |
| R5 | The `/translate` route stays unavailable or the chat route changes | Medium | Low | Both routes supported; the fallback switches after the first 404 |
| R6 | YouTube changes how it serves videos and the pinned yt-dlp stops working, or asks to confirm the viewer isn't a bot | Medium | Low | Optional and off by default; clear messages; the README says how to update yt-dlp; uploads always work |

### Dependencies

- Bodhan keys with credit: one for `indic-transcribe`, and one for `indic-translate` to translate
- Python 3.10+ and Node.js 20.19+ on the reviewer's machine
- A native speaker per language to rate output quality

### Open questions

- [ ] Q1: Can `indic-transcribe` fill the `words` or `segments` fields of `verbose_json`? (Bodhan)
- [ ] Q2: Is `POST /translate` going to be available, or should recipes use chat completions? (Bodhan)
- [ ] Q3: Should the app remind the creator, before upload, that they must have the right to subtitle the video? (Maintainers)
- [ ] Q4: Should translation work on whole pieces rather than single lines, accepting less precise timing for translated lines in exchange for better translations and far fewer calls? (Reviewers)

## 15. Open source and contribution

| Item | Answer |
| --- | --- |
| Code location | `examples/subtitle_generator/` in the cookbook |
| Licence | Apache-2.0, the cookbook's licence |
| Third-party licences | Backend: FastAPI and fastapi-cli (MIT), Uvicorn (BSD-3-Clause), httpx (BSD-3-Clause), pydantic-settings (MIT), python-multipart (Apache-2.0), SQLModel (MIT), SQLAlchemy (MIT), imageio-ffmpeg (BSD-2-Clause), silero-vad-lite (MIT, Silero model MIT). imageio-ffmpeg downloads a static FFmpeg build at install time (GPL); it is not committed to the repo. YouTube links: yt-dlp (Unlicense), yt-dlp-ejs (Unlicense, MIT and ISC), Deno (MIT); the page uses YouTube's IFrame Player API under YouTube's API terms. Web app: React (MIT), Vite (MIT), lucide-react (ISC). Fonts from Google Fonts: Poppins and Inter (SIL Open Font License) |
| README | Has Setup, Run, How it works and Limitations sections |
| Configuration | `.env.example` declares the Bodhan keys and every other setting; `.env` is git-ignored |
| Dependencies | Pinned: `backend/requirements.txt` (`name==version`) and `frontend/package.json` with its lock file |
| Tests | 99 backend tests, offline (no key or network needed; Bodhan, YouTube and links are faked) |
| AI assistance | Claude Code (most of the code, tests and docs, directed and reviewed by the author) and Antigravity (an architecture review); declared in the PR |

**Areas left for contributors:** editing line timings, word-level forced alignment, deleting jobs from the history, burned-in subtitles, several translation languages at once, and the evaluation script in Section 12.

## 16. Review

*Filled in at the review meeting.*

### Reviewer checklist

- [ ] The problem and primary user are specific, with evidence
- [ ] V0 scope proves one clear value
- [ ] Every Must requirement has a testable acceptance criterion
- [ ] Every Bodhan model and language used is supported today
- [ ] Audio formats, long inputs and API errors are handled
- [ ] No Bodhan key reaches the browser
- [ ] Personal data, children's data, consent and deletion are covered
- [ ] The cost estimate is worked through
- [ ] Sample and demo data are synthetic or consented
- [ ] Licence and code location are agreed

### Comment log

| # | Section | Comment | Status | Resolution |
| --- | --- | --- | --- | --- |
| | | | | |

### Decision

| Field | Value |
| --- | --- |
| Decision | |
| Conditions | |
| Next step | Architecture review of [ARCHITECTURE.md](ARCHITECTURE.md) |
