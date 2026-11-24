# Video dubbing

> Dub an English video into Indian languages, with synced AI speech and subtitles, using Bodhan's Indic models.

**Bodhan models:** indic-transcribe, indic-translate, indic-speak
**Languages shown:** Hindi, Tamil, Bengali
**Format:** CLI, plus a Streamlit web app

- **Try it:** [bodhan-video-dubbing.streamlit.app](https://bodhan-video-dubbing.streamlit.app/) (bring your own keys)
- **Demo video (3 min):** [Google Drive](https://drive.google.com/file/d/1gJHxb4Hsz58iMBYXHVE2VQ8zeVU0nTmh/view?usp=sharing)
- **Demo outputs:** [Google Drive](https://drive.google.com/drive/folders/1McNUfZCdxGZ3G-xNPWh6wE1FMpRNA4Jw?usp=sharing)
- **Design notes, architecture and flow chart:** [DESIGN.md](DESIGN.md)

## Features

- **Input:** an English MP4, MOV, MKV or WebM video, 1 to 15 minutes, one or more speakers.
- **Output, per language:** a dubbed MP4, subtitles, and a per-sentence trace; plus the English transcript and a run report.
- **Languages:** any of Indic-Speak's 13 production-tier languages: `hi bn mr te ta gu kn ml or pa as ur`, and `en` (re-voiced).
- **In sync:** each sentence starts within 0.3 s of the original; long lines are shortened before they are sped up, and output length matches the input.
- **Checked by listening:** every spoken line is transcribed back, and lines with missing or repeated words are regenerated.
- **Consistent terms:** names, numbers and technical terms stay the same throughout; a glossary can pin them.
- **Background kept:** music and effects stay; silence stays silent.
- **Report:** every quality target per language, met or missed and why, with retries, latency and cost.
- **Robust:** one language failing does not stop the others; the run still finishes if the LLM is missing or down.

Bonus:

- Speaker-aware voices: up to two speakers told apart by pitch, each keeping one voice.
- Resumable runs: every API call is cached, so a rerun is free; languages run in parallel.
- Code-mixed translation (`--code-mixed`), such as Hinglish.
- A web app over the same pipeline, deployed on Streamlit Community Cloud.

The output is labelled AI-dubbed. Voices come from Indic-Speak's library; the original speaker's voice is not imitated.

## Setup

Needs Python 3.12+ and `ffmpeg`.

```bash
cd examples/video_dubbing
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # add your Bodhan keys and, optionally, an LLM
```

Or with Docker (includes ffmpeg):

```bash
docker build -t bodhan-dub .
docker run --rm --env-file .env -v "$PWD/data:/data" bodhan-dub --input /data/video.mp4 --langs hi,ta,bn --out /data/out
```

The LLM is any OpenAI-compatible endpoint (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`) and works on the English text only. Tested with `gpt-oss-120b` on AWS Bedrock and with Gemini.

## Run

```bash
python -m bodhan_dub --input path/to/video.mp4 --langs hi,ta,bn --out ./out
```

| Option | What it does |
|---|---|
| `--glossary file.yaml` | `keep:` terms stay as written in every language; `terms:` are spelled consistently. |
| `--voices file.yaml` | Pick voices per language: `hi: Amit`, `ta: female`, or `bn: {female: Ishita, male: Sourav}`. |
| `--code-mixed` | Code-mixed translation, keeping common English words. |
| `--separate auto\|on\|off` | Separate music from voice with Demucs (default: only when there is music). |
| `--max-minutes N` | Refuse longer videos before spending anything (default 15.5). |

```yaml
# glossary.yaml
keep: [Voyager, NASA]
terms: [linear pair, vertically opposite angles]
```

Output:

```
out/
  report.json          # status and quality targets per language, calls, retries, latency, cost
  run.jsonl            # structured log
  transcript.en.json   # English sentences with timings
  subtitles.en.srt
  hi/
    video.hi.mp4
    subtitles.hi.srt
    segments.hi.json   # per sentence: English, translation, voice, timing, speed, QA
```

Web app locally: `pip install -r requirements-app.txt && streamlit run app.py`.

## How it works

1. Find speech (Silero VAD, with Demucs for music) and transcribe it with Indic-Transcribe.
2. The LLM tidies the English and marks sentence ends; code joins fragments into sentences and checks every edit.
3. Translate all sentences together with Indic-Translate.
4. Speak each sentence with Indic-Speak and check it by transcribing it back.
5. If a sentence is too long for its slot, the LLM shortens the English and it is translated and spoken again; anything still long is sped up.
6. Place each clip at its original time, keep the background, set loudness to -16 LUFS, and mux.

## Results

The demo: a 5.5-minute Class 9 maths lesson in Hindi, Tamil and Bengali.

| Target | Hindi | Tamil | Bengali |
|---|---|---|---|
| Sync (start within 0.3 s) | met | met | met |
| Speed (up to 1.2x) | met | 2 of 36 lines above (max 1.35x) | 2 of 36 lines above (max 1.35x) |
| Coverage | 36 of 36 | 36 of 36 | 36 of 36 |
| Back-transcription CER (median) | 0.050 | 0.056 | 0.045 |
| Length (within 0.1 s) | 0.0 s | 0.0 s | 0.0 s |
| Loudness (-16 LUFS) | -16.0 | -16.1 | -16.4 |

Cost and time with an empty cache: INR 8.58 and 22.6 minutes for all three, about INR 0.52 and 82 seconds per minute of video per language. Most of the time is Indic-Speak's rate limit.

## Tests

```bash
pytest tests   # unit tests, plus a 30-second end-to-end test when .env has Bodhan keys (about INR 0.30)
```

## Limitations

- A few Tamil and Bengali lines stay above 1.2x: these languages run about 1.5x longer than English, and the English is not cut below 60% of its words.
- Shortened English can be terse ("Lines AB CD cross O; ray OE on B side."), depending on the LLM.
- Speakers are told apart by pitch only, so two speakers of the same gender share a voice.
- On-screen text (slides, captions) is not translated.
- Speed is bound by Indic-Speak's rate limit (about 8 requests a minute).
