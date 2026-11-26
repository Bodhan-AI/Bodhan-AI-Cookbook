# Bodhan Live Interpreter

Live translation for Google Meet and Zoom on Windows, built entirely on Bodhan.AI's Indic models.

- **Subtitles:** what the other side says (e.g. Hindi) appears as English subtitles in a floating bar.
- **Interpreting:** hold a key, speak English, and the meeting hears you in Hindi.
- **Optional dubbing:** also *hear* their side in English, in your earphones.

It uses three Bodhan models: Indic-Transcribe (speech → text), Indic-Translate and Indic-Speak (text → speech). Silero VAD runs locally to cut speech into sentences. Optionally, Indic-Transcribe can run on your own NVIDIA GPU.

**New here?** Follow [Setup](#setup) and then [Run](#run) from top to bottom. A first test with a YouTube video takes about 20 minutes, most of it installing.

## How it works

```
THEIR SIDE  meeting audio (Windows default output)
            → WASAPI loopback → Silero VAD → Indic-Transcribe → Indic-Translate → subtitle bar
                                                                       └→ (optional) Indic-Speak → your earphones

YOUR SIDE   your mic, only while push-to-talk is held
            → Silero VAD → Indic-Transcribe → Indic-Translate → Indic-Speak → VB-Cable → the meeting's mic
```

- **VB-Cable:** the meeting app (Meet or Zoom) uses VB-Audio's virtual cable as its microphone, so the other side hears your *translated* voice.
- **Recording:** every session is saved as a bilingual transcript.

## Features

| Feature | What it does |
|---|---|
| Subtitles | Their speech, transcribed and translated, shown about 1 s after they pause. The transcript appears first, then the translation fills in. |
| Sentence merging | When a pause splits a sentence ("for now I / cut the call"), the second half joins the first and the pair is re-translated as one sentence. |
| Interpreting | Hold **Right Ctrl**, speak; the meeting hears you in their language. Long sentences play in parts, so speech starts within 1.5–2.5 s. |
| Dubbing (optional) | Their translated subtitles spoken to you, on a separate output such as earphones. Off by default; toggle from the bar. |
| Local speech recognition (optional) | Indic-Transcribe on your NVIDIA GPU: free, offline, no rate limits. |
| Live captions (local only) | A provisional translation every ~2 s while they're still talking, replaced when they pause. |
| Session record | Every meeting saved as JSONL, plus a Markdown transcript and SRT subtitles. |
| Settings window | Languages, voices with preview, push-to-talk key, devices, engine, dubbing, and a live latency readout. |

## Setup

You do this once. All commands are for **PowerShell** on Windows.

### What you need

- **Windows 10 or 11.** The app captures system audio with WASAPI, so it doesn't run on macOS or Linux.
- **Python 3.11 or 3.12** from [python.org](https://www.python.org/downloads/). In the installer, tick **"Add python.exe to PATH"**.
- **Bodhan API keys.** In the [Bodhan console](https://console.bodhan.ai/dashboard), create a key for each of the three models: Indic-Transcribe, Indic-Translate and Indic-Speak. Keys are per model. If one key covers all three for you, you can use `BODHAN_API_KEY` instead.
- **VB-Audio Virtual Cable** (free), for interpreting.
- **Headphones or earphones,** recommended for meetings.
- **Optional:** an NVIDIA GPU, for local speech recognition and live captions.

### Step 1: Get the code

```powershell
git clone https://github.com/Bodhan-AI/Bodhan-AI-Cookbook.git
cd Bodhan-AI-Cookbook\examples\live_meeting_interpreter
```

No git? On the repository page, choose **Code → Download ZIP**, unzip it, and `cd` into `examples\live_meeting_interpreter`. **Run every command below from this folder.**

### Step 2: Install VB-Audio Virtual Cable

1. Download it from [vb-audio.com/Cable](https://vb-audio.com/Cable/), unzip it, right-click `VBCABLE_Setup_x64.exe` → **Run as administrator**, click **Install Driver**, then **reboot**.
   - It adds a virtual speaker, "CABLE Input", which the app plays your translated voice into.
   - It also adds a virtual microphone, "CABLE Output", which the meeting uses as your mic.
2. **The installer makes the cable your Windows default speaker and microphone. Change them back.** Press **Win+R**, type `mmsys.cpl`, press Enter, then:
   - On the **Playback** tab, right-click your speakers or headphones → **Set as Default Device**.
   - On the **Recording** tab, right-click your real microphone → **Set as Default Device**.
   - Use "Set as Default Device", not "Set as Default Communication Device". The second one only changes the default for calls, which the app doesn't use.

### Step 3: Create the Python environment

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate          # your prompt now starts with (.venv)
python -m pip install --upgrade pip
```

If PowerShell refuses to run `activate` ("running scripts is disabled"), run this once, then try again:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Step 4: Install PyTorch, then the app's packages

PyTorch has to be installed first, from its own download index. Pick **one** of the two commands:

```powershell
# No NVIDIA GPU, or you won't use local speech recognition (smaller download):
pip install torch --index-url https://download.pytorch.org/whl/cpu

# NVIDIA GPU: match the CUDA build to your driver. Run nvidia-smi and read "CUDA Version".
# 12.6 or newer (e.g. driver 566 shows 12.7):
pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu126
```

Then:
```powershell
pip install -r requirements.txt
```

### Step 5: Configure `.env`

```powershell
copy .env.example .env
notepad .env
```

Fill in:
- **Keys:** `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`.
- **Languages:**
  - `MY_LANGUAGE`: what you speak, and the language your subtitles appear in. Default `en`.
  - `THEIR_LANGUAGE`: what the meeting speaks, and the language your translated voice uses. Default `hi`.
  - Codes are ISO codes like `hi`, `ta`, `bn`, `mr`.
- Leave `BODHAN_TRANSLATE_RPM=15` and `BODHAN_SPEAK_RPM=7` as they are. These are the limits Bodhan actually enforced in testing (see [Rate limits](#rate-limits)).

Everything else can stay as it is, and most of it can be changed later in the app's settings window. `.env` holds your keys: never share it or commit it. It's already in `.gitignore`.

### Step 6: Check the setup

```powershell
python -m pytest -q           # unit tests; no keys, network or devices needed
python -m tools.smoke_test    # one real call to each Bodhan model; writes out\smoke_tts.wav
python -m tools.list_devices  # your audio devices; "CABLE Input" and "CABLE Output" must be listed
python -m tools.cable_check   # plays a beep through VB-Cable and checks your Windows defaults
```

**What you should see:**
- `smoke_test` prints three timed lines:
  - `TTS`: it synthesised a sample sentence.
  - `ASR`: it transcribed that audio back.
  - `Translate`: it translated the transcript.

  If you get `401`, a key is wrong, or it's the right key in the wrong variable.
- `cable_check` prints **PASS** and no **FIX** lines. It sends a one-second beep into the cable, so don't run it while you're in a meeting that uses the cable as its mic.

## Run

Always start from the project folder, with the environment active:

```powershell
cd <path>\Bodhan-AI-Cookbook\examples\live_meeting_interpreter
.venv\Scripts\activate
```

### First run: subtitles for a video (no meeting needed)

1. Play a Hindi video on YouTube (a news clip or an interview works well).
2. Run:
   ```powershell
   python -m app.main --mode subtitles
   ```
3. A dark bar appears at the bottom of the screen. Within about a second of each pause, it shows what was said and then the English translation.
4. To stop, **right-click the bar → Quit**.

### In a meeting: subtitles and interpreting

1. Set up Meet or Zoom as described in [Live meeting setup](#live-meeting-setup): the meeting's microphone must be **CABLE Output**.
2. Start the app with both directions:
   ```powershell
   python -m app.main
   ```
3. **Their side:** subtitles appear on the bar as they speak.
4. **Your side:** **hold Right Ctrl**, say a sentence in your language, and let go. The other side hears it in theirs about 1.5–2.5 s later.
5. To stop, right-click the bar → **Quit**. Your transcript is saved in `out\sessions\`.

### Command-line options

```powershell
python -m app.main                                # subtitles + interpreting (use this for meetings)
python -m app.main --mode subtitles               # subtitles only: the meeting won't hear you
python -m app.main --mode interpret               # interpreting only
python -m app.main --log-file out/meet.log        # also keep a log, for tools.latency_report
python -m app.main --log-level DEBUG              # more detail in the terminal
```

### Using the subtitle bar

There is one window: **the subtitle bar** at the bottom of the screen. It is always on top.
- Drag the bar with the mouse.
- **Right-click it** for **Settings…**, the **Speak their translation to me** switch, and **Quit**.
- The status line tells you what's on, e.g. **"Hold Right Ctrl to talk (English → Hindi)"**. "Subtitles only (interpreting off…)" means you started with `--mode subtitles`, so the meeting won't hear you.
- The terminal prints per-sentence timings (`asr 0.3s mt 0.2s tts 1.4s | e2e 1.9s`). Keep it open: closing it stops the app.

**Subtitles appear in two steps:** first what was said, with "…" in place of the translation, then the translation. If the speaker pauses briefly after a short fragment (up to about 2.5 s of speech), their next part joins it, and the caption is re-translated as one sentence. Merges join at most two pieces. Set `SUBTITLE_MERGE_PAUSE_S=0` to turn merging off.

### The settings window

Right-click the bar → **Settings…**:

- **Languages:** what you speak, what they speak, and the subtitle script.
- **Interpreting (what they hear):** voice and style, with **Preview** (plays a sample line on your speakers), and the push-to-talk key (press **Change…**, then the key).
- **Devices:** your microphone and the VB-Cable input the meeting listens to.
- **Their side, spoken to you:** dubbing; see below.
- **Speech recognition:** Bodhan cloud or your GPU, and live captions. Underneath is a readout of recent timings per stage.

**Save and restart:**
- It writes only the settings you changed to `.env`. Your API keys and comments are left alone, and the file is replaced atomically.
- Then it restarts the pipelines in a few seconds, about 10 s with local speech recognition.
- A setting you also set in the PowerShell window that started the app (e.g. `$env:ASR_BACKEND`) wins over `.env`. The window shows it as locked.

### Session transcripts

Every session is saved to `out/sessions/<date>_<time>`:
- `.jsonl`: every final caption in both directions, written as the meeting goes.
- `.md`: a readable bilingual transcript.
- `.srt`: subtitles with the translation over the original.

The Markdown and SRT files are written when you quit. `python -m tools.export_session --latest` rebuilds them, e.g. after a crash. SRT times count from when the app started. Set `SESSION_RECORD=false` to keep nothing.

## Live meeting setup

**Before starting the app:**
- **Plug in your headphones or earphones first.** The subtitles listen to whatever Windows' default output is when the app starts. Windows often switches the default to newly plugged earphones, so check it again afterwards.
- **Check the Windows defaults** (`mmsys.cpl`): output is your speakers or headphones, input is your real microphone. Never make the cable the default output.

**Google Meet** (⋮ → Settings → Audio):
- Microphone: **CABLE Output (VB-Audio Virtual Cable)**
- Speaker: the device that is Windows' default output (with dubbing: **Speakers**; see below). On ASUS laptops, **not** "AI Noise-cancelling Output".
- Leave Meet's own **Push to talk** off and keep your mic **unmuted** in Meet. The app only sends audio while you hold the key.
- Turn **noise cancellation off** if your account shows it: it can clip synthetic speech.

**Zoom** (Settings → Audio):
- Microphone: **CABLE Output**
- Speaker: your headphones or speakers (the Windows default output)
- Untick **Automatically adjust microphone volume**, and set background noise suppression to **Low**.

**How it behaves:**
- **Push-to-talk:** hold the key the whole time you speak, then let go. The other side hears nothing unless the key is held.
- **Sentences:** speak in sentences with short pauses. Each pause sends that sentence while you keep talking.
- **Language:** speak the language set as `MY_LANGUAGE`. Speaking Hindi while it's set to English produces made-up English words. Romanised Hindi phrases ("mujhe ice cream bahut pasand hai") come through fine.
- **Your real voice:** the meeting hears only your translated voice, because the cable is your meeting mic. To send your real voice too, mix both with VoiceMeeter.

**Demo tip:** join the same Meet from a phone as "the other person" and speak Hindi into it. Keep the phone in another room, or muted while you speak English. Otherwise it sends your English back and the subtitles try to read it as Hindi.

## Optional: hear their side spoken (dubbing)

By default you *read* what the other side says. You can also hear their translated subtitles spoken in your earphones.

**It needs an output the subtitles can't hear.** The subtitles listen to Windows' default output, where the meeting plays. So the dub must play on a *different* device. A setup that works (tested in a real Meet):

| | Device |
|---|---|
| Windows default output | **Speakers**: you hear their original voice from the laptop |
| Meet → Speaker | **Speakers** |
| Settings → Their side, spoken to you → Play on | **Headphones / earphones**: you hear the English dub |

**Setup:**
1. Plug in the earphones and run `python -m tools.list_devices`. They must show up as their own output, e.g. "Headphones (Realtek(R) Audio)". On some laptops they just take over the speakers instead, and then this can't work.
2. Make **Speakers** the default output again in `mmsys.cpl`.
3. Start the app → right-click → Settings… → "Their side, spoken to you": choose **Play on**, a **Voice** (with Preview), tick **Speak their translation to me**, then **Save and restart**.

**Save tests the device first.** It plays a 1.2 s voice clip on the device and measures whether the subtitle capture hears it. If it does, the device is refused with an explanation. The test uses speech because noise-cancelling outputs filter out test tones. A device set by hand in `.env` is tested once at startup. Results are kept in `out/dub_device_checks.json`. VB-Cable endpoints and the default output are always refused.

**Using it:** right-click the bar → **Speak their translation to me** switches it on and off instantly, without a restart. Subtitles keep running either way.

## Optional: speech recognition on your own GPU

The app can run Bodhan's open **Indic-Transcribe Core** model on an NVIDIA GPU instead of the hosted API. Translation and speech still use Bodhan's API.

| | Hosted (default) | Local (`ASR_BACKEND=local`) |
|---|---|---|
| Transcripts | Indic-Transcribe | The same model: 8 of 8 and 13 of 14 test sentences identical to hosted |
| Time per sentence (RTX 3050 Ti, 4 GB) | 0.2–0.3 s (0.5 s for a 9 s sentence) | 0.3–0.5 s (1.0–1.2 s for a 9 s sentence) |
| Cost and limits | Billed per second, rate-limited | Free, no limits, works offline |
| Startup | Instant | About 15 s to load the model (2.3 GB of VRAM) |

**Setup, once:**
```powershell
pip install -r requirements-local-asr.txt
pip install --no-deps torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu126
hf auth login        # a Read token; your Hugging Face account needs access to the gated model
hf download bodhan-ai/indic-transcribe-core --exclude "nemo/*" --local-dir models/indic-transcribe-core
```

**Then:**
- Choose "This PC's GPU" in Settings, or set `ASR_BACKEND=local` in `.env`. `BODHAN_TRANSCRIBE_KEY` can then be empty.
- Version pins that matter: **transformers must be below 5**. With 5.x, the transcript derails after the first word into other scripts or "वववव…". torchaudio stops at 2.11 and is installed without dependencies, so it can't replace torch.

**Live captions (local only).** While someone speaks a long sentence:
- The bar shows a provisional translation every ~2 s, ending in "…". The final caption replaces it when they pause.
- Each refresh costs one translation request. Refreshes pause whenever more than half the translate key's per-minute limit is in use, so final captions always come first.
- `LIVE_CAPTION_INTERVAL_S=0` turns them off.

## Tools

| Command | What it does |
|---|---|
| `python -m tools.smoke_test` | One round trip through all three Bodhan APIs |
| `python -m tools.list_devices` | Windows audio devices, defaults, and which are VB-Cable |
| `python -m tools.cable_check` | Tone through VB-Cable, plus a check of the Windows defaults |
| `python -m tools.vad_probe --seconds 60 [--sweep] [--mic]` | Record and tune voice detection offline (no API calls) |
| `python -m tools.latency_report <log>` | Per-stage medians, live captions, dubbing, and retries/429s for a session log |
| `python -m tools.subtitle_replay <wav>` | Replay a recording through the subtitle pipeline at real-time pace (makes real API calls) |
| `python -m tools.asr_bench <folder> [--hosted]` | Local GPU speech recognition: VRAM, speed, and hosted side by side |
| `python -m tools.export_session --latest` | Rebuild a session's Markdown and SRT |

**Tuning voice detection:**
- Start a Hindi video, then run `python -m tools.vad_probe --seconds 60`.
- Then run `python -m tools.vad_probe --wav out/probe.wav --sweep` to compare settings on the same recording.
- If music or sound effects come through as speech, raise `VAD_THRESHOLD`; 0.65 worked for a Hindi film. If short replies like "haan" go missing, lower it.

## Measured performance

All on one laptop (RTX 3050 Ti), against the live Bodhan API, November 2026.

| | Measured |
|---|---|
| Subtitles, hosted speech recognition | Transcript 0.3 s and translation 0.5 s (medians) after the speech is cut, which is 0.6 s after the speaker pauses. So you read it about 1.1 s after they stop. |
| Subtitles, local speech recognition | Translation 0.56–0.73 s (median) after the cut |
| Interpreting (Meet) | First Hindi audio a median 1.64 s after you let go of the key (short sentences) |
| Dubbing (Meet) | Heard a median 2.3 s after they finish a line (max 4.4 s) |
| Live captions | ~1 s to produce each refresh |

## Rate limits

Every request, including every retry, goes through a client-side limiter:
- `BODHAN_RPM` requests a minute, with per-model overrides (`BODHAN_TRANSCRIBE_RPM`, `BODHAN_TRANSLATE_RPM`, `BODHAN_SPEAK_RPM`)
- `BODHAN_CONCURRENCY` requests in flight
- up to `BODHAN_MAX_RETRIES` retries, with backoff that honours `Retry-After`. Retries stop once a result would be too late (`SUBTITLE_DEADLINE_S`, `INTERPRET_DEADLINE_S`).

**What Bodhan actually enforced in testing (sliding 60 s window, per model key):**

| Key | Advertised | Enforced | Set in `.env` |
|---|---|---|---|
| Translate | 120/min, 10 in parallel | **~16/min** (the 17th within a minute got 429, Retry-After 11 s) | `BODHAN_TRANSLATE_RPM=15` |
| Speech | nothing published | **~8/min** (the 9th within a minute got 429) | `BODHAN_SPEAK_RPM=7` |
| Transcribe | nothing published | not reached in testing | default (50) |

**How the app shares this budget:**
- Final subtitles take priority over live captions: live captions only translate while half the translate limit is free.
- Your interpreting takes priority over dubbing: dubbing only speaks while half the speech limit is free.
- `python -m tools.latency_report` lists any 429s.

## Shortcomings and known limitations

**Bodhan API limits (the biggest constraint today):**
- **About 16 translations and 8 speech requests per minute**, far below the advertised 120. In a lively conversation:
  - A subtitle can wait several seconds for a free translation slot.
  - Interpreting (1–3 speech requests per sentence) and dubbing compete for 8 requests a minute, so most dub lines get skipped.
  - Before the limit was set correctly, one interpreted sentence failed and two arrived 21–27 s late.
- **Mismatches with the API docs:**
  - The documented `POST /translate` returns 404, so the app uses the documented alternative, `/v1/chat/completions` with model `indic-translate`.
  - The rate-limit header names differ from the docs, and their numbers aren't what's enforced.
  - The speech response says `audio/mpeg` but is WAV.

**Speech recognition and translation quality:**
- **No punctuation** in transcripts, and translations mirror that.
- **Pauses split sentences.** Merging can only use the pause length, so it sometimes joins two separate short lines, or a fragment is translated on its own ("you to us / not audible").
- **Names suffer:** "गौरव" (Gaurav) was translated as "George" and "पृथ्वी जी" (Prithvi ji) as "earth". English names get misspelled too. Keep names out of demo scripts.
- **Language must match:** speech in a language other than the configured one comes out garbled, e.g. Hindi with `MY_LANGUAGE=en`. English spoken on the meeting side is transcribed in Devanagari, though translation mostly recovers it.
- **Not word by word:** no streaming from the API, and at most 30 s per request. Subtitles appear after each pause; the exception is live captions, which need local speech recognition.

**Latency:**
- **Speech synthesis isn't streamed:** about 0.07 s per character, roughly the time the audio takes to play. Interpreting splits long sentences to start sooner, but a long sentence still takes several seconds in total.
- **Dubbing lags:** each line plays a few seconds after they finish it. Spoken English takes about as long as the original, so in a long monologue, lines that fall too far behind are skipped.

**Dubbing:**
- It needs a second output that the subtitle capture can't hear. Many laptops can't do this with wired earphones (the jack just redirects the speakers). Virtual outputs such as ASUS's "AI Noise-cancelling Output" are mixed back into the speakers; the app detects and refuses them.
- When a merged sentence arrives after its first half was already spoken, that half is heard twice.

**Platform and setup:**
- **Windows only:** WASAPI loopback, VB-Cable and global hotkeys.
- **The meeting hears only your translated voice,** unless you add VoiceMeeter.
- **Push-to-talk only:** there is no automatic turn-taking.
- **The app listens to the default output from when it started.** Switching Windows' default output mid-meeting needs a restart, e.g. from Settings.
- **Local speech recognition** needs an NVIDIA GPU, a CUDA build of torch matching your driver, `transformers<5`, a torchaudio workaround, and 4.9 GB of gated weights. It's about twice as slow per sentence as hosted. Translation and speech can't run locally alongside it in 4 GB of VRAM.
- **Runs from source only:** you need Python and the steps in [Setup](#setup).

**The subtitle bar:**
- **One caption at a time:** a new subtitle can replace your own interpreted caption on screen.
- **Fixed height (150 units):** a very long caption on a small or low-resolution screen can cut off the status line.
- **Not DPI-aware:** Tk renders slightly soft at 125% scaling.
- **Language codes:** the label says "Them (hi → en)" rather than language names.
- **SRT timing:** times count from app start, so lining them up with a separate recording of the meeting needs an offset.

## Troubleshooting

| What you see | Cause and fix |
|---|---|
| The other side can't hear you | Did you start without `--mode`? Does the bar say "Hold Right Ctrl…"? Are you holding the key while you speak? Is Meet's mic CABLE Output and unmuted? |
| "Stopped: Windows default output is VB-Cable…" | The cable is the default output. Set your speakers or headphones as the default playback device in `mmsys.cpl`. |
| "Interpreting off: mic is VB-Cable…" | The cable is the default input. Choose your real mic in Settings, or set it as the Windows default recording device. |
| No subtitles | Check that the meeting plays through Windows' default output, and that it was the default when the app started. `python -m tools.list_devices` shows the default. |
| Subtitles for background noise or music | Raise `VAD_THRESHOLD`; `tools.vad_probe --sweep` helps choose. |
| A sentence was never interpreted, or arrived very late; 429 in the log | The speech key's limit. Keep `BODHAN_SPEAK_RPM=7`, and switch dubbing off when you need to talk a lot. |
| "Speak their translation" is greyed out | No safe dub device yet: choose one in Settings. Or the chosen device failed the leak test, and the status line says why. |
| "Stopped: No Indic-Transcribe weights…" or "…needs an NVIDIA GPU…" | Local speech recognition is selected, but the model isn't downloaded or torch is the CPU build. See the GPU section, or choose Bodhan cloud in Settings. |
| Local transcripts turn into other scripts or "वववव…" | transformers 5.x is installed: `pip install "transformers>=4.44,<5"`. |
| A setting is locked in the settings window | It's set in the PowerShell window that started the app (e.g. `$env:ASR_BACKEND`). Close that window, or `Remove-Item Env:ASR_BACKEND`, then start again. |

## For developers

- `python -m pytest -q`: tests need no network, devices or keys.
- **Layout:**
  - `app/bodhan`: the API client, rate limiter and language lists.
  - `app/audio`: capture, voice detection, playback, leak check.
  - `app/pipeline`: subtitles, interpreting, dubbing and text helpers.
  - `app/ui`: the subtitle bar and the settings window.
  - `app/main.py`: wiring and restart.
  - `tools/`: the command-line tools above.
- **Invariants the code keeps:**
  - Every HTTP attempt to Bodhan, retries included, goes through the rate limiter.
  - Audio sent to transcription stays under 30 s.
  - The language is always passed to transcription.
  - Results are delivered in order, and stale ones are dropped.
  - Keys are never logged.
  - The Tk thread and the audio callback never block.
- **The API as observed, where it differs from the docs:**
  - Translation uses `/v1/chat/completions` with `model=indic-translate`, because `POST /translate` returns 404.
  - Translate and speech requests need a `lang`/`target_language`.
  - The live gateway's rate-limit headers are named `x-ratelimit-api_key-limit-*`.
