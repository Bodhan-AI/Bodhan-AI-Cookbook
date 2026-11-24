# Video Dubbing Pipeline

Dub an English video with Bodhan Indic-Transcribe, Indic-Translate and Indic-Speak. The required command produces MP4s, subtitles, an English transcript and per-language reports. An OpenAI-compatible LLM plans English grouping and duration recovery; it never writes target-language text or receives audio.

## Setup

Use Python 3.10–3.13 and install system `ffmpeg`/`ffprobe`. From this recipe directory:

```sh
sh scripts/setup.sh
source .venv/bin/activate
```

The script creates a local environment, installs pinned requirements and the package, and selects CPU PyTorch on Linux. On macOS, a compatible Python must be the `python3` on PATH. An optional project-local media bootstrap remains in `scripts/setup_local_media.py`; see `docs/media_compatibility.json` for the actual downloaded binary versions. Processing never installs tools automatically.

Export `BODHAN_TRANSCRIBE_KEY`, `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY`. Alternatively supply `--env-file /private/path/.env`. The file is loaded only when explicitly named. Never commit keys or media. OCR is not part of the core pipeline.

The supplied Dockerfile uses Python 3.12 and CPU PyTorch:

```sh
docker build -t bodhan-dub .
docker run --rm \
  --mount type=bind,src=/private/path/.env,dst=/run/secrets/bodhan.env,readonly \
  -v /path/to/media:/media:ro -v /path/to/results:/results \
  bodhan-dub --input /media/English.mp4 --langs hi,ta,bn --out /results/run \
  --env-file /run/secrets/bodhan.env
```

Docker setup is verified on Linux ARM64: the image builds, dependency consistency and bundled offline VAD pass, all 507 recipe tests pass inside the container, and the default entrypoint prepares the real demo without API calls. See [Docker validation and reproducible commands](docs/docker_validation.md). Linux AMD64 and a literal fresh clone of the final commit have not been tested.

## Run

```sh
python -m bodhan_dub --input /path/to/English.mp4 --langs hi,ta,bn --out ./out
```

This command makes paid Bodhan calls. The default run tracks estimated spend throughout. Pass `--max-cost-inr` with a rupee amount if you want a spending limit. Calls reserve their maximum estimated charge before starting. Unknown outcomes retain the reservation and are not retried automatically. Known temporary failures use bounded retries/backoff; request pacing and concurrency are bounded. Successful provider outputs are cached and stage journals are retained.

Input: MP4/MOV/MKV/WebM, 1–15 minutes. The first usable video and first audio stream are selected. Use `--allow-short-input` only for synthetic smoke fixtures. Supported production targets: `en,hi,bn,mr,te,ta,gu,kn,ml,or,pa,as,ur`. Unknown/duplicate codes fail before API calls.

```text
out/
  report.json
  transcript.en.json
  budget.json
  hi/video.hi.mp4
  hi/subtitles.hi.srt
  hi/segments.hi.json
  ta/...
  bn/...
  _stages/               # source windows, raw responses, corrections, translations, QA
  _fit/                  # measured fit decisions and recovery evidence
  _render/               # rendering report and per-segment events.jsonl
```

Exit status is nonzero if any requested language fails. Complete languages retain their videos and subtitles. A language with unfitted narration is reported as failed rather than finalized with missing speech. Choose a fresh output folder; existing results are not overwritten.

Translation meaning checks are batched by input size, and English planning retries temporary provider errors.

### Configuration

Optional `--voices voices.yaml` uses documented voice names:

```yaml
hi: Kavya
ta: Anitha
bn: Ishita
```

Optional `--glossary glossary.yaml` supplies preferred target terms as context to Bodhan:

```yaml
hypotenuse:
  hi: कर्ण
```

The optional reference-backed mathematics example is `sample_data/geometry_glossary.yaml`; its sources are recorded in `docs/geometry_glossary_sources.json`. It is never selected automatically for unrelated videos. The preferred term is checked in Bodhan's response. A mismatch is reported; Python does not rewrite the translation. English glossary terms are protected during condensation. YAML is safely parsed with size/shape limits. `--code-mixed` uses Bodhan's documented `codemix` translation route; this optional route has mocked contract tests but has not been live-validated here. English is not a supported target in this mode.

### Offline reuse and development review

```sh
python -m bodhan_dub --input /path/to/English.mp4 --langs hi,ta,bn --out ./out-review \
  --from-run /path/to/saved/run --fit-manifest /path/to/fit_manifest.json
```

This path validates the fitted audio hashes and coverage before rendering, and makes no hosted calls. For a new full pipeline execution, optional `--cache-dir /path/to/persistent/cache` reuses validated provider results across fresh output folders. This does not overwrite previous reports or bypass unknown-outcome protection. Shared-cache runs are labeled separately and do not establish a fresh hosted benchmark. Preparation-only and detection-only modes remain available with `--prepare-only` or `--detect-speech-only`. Review utilities (`bodhan_dub.review`, `bodhan_dub.window_review`, `bodhan_dub.serve_review`) are optional local developer tools. They are not needed by evaluators.

## Design and code guide

- [Architecture, workflow, recovery decisions and research references](docs/design.md)
- [Code walkthrough: brief syntax explanations and detailed purpose](docs/code_walkthrough.md)
- [Current checklist and remaining quality work](docs/pipeline_checklist.md)
- [Every assignment quality target and engineering expectation](docs/quality_engineering_audit.md)
- [Assignment submission checklist and recording outline](docs/submission.md)
- [PR description draft using the assignment headings](docs/pr_description_draft.md)

Source timing combines Bodhan window offsets and local VAD. No other ASR or alignment model is used. Words are grouped into connected thoughts; labels and numbers are guarded before English condensation. Target text is always produced by Bodhan. Duration recovery measures actual audio, trims near-digital silence, preserves long-pause cores, applies at most 1.2× tempo, and only then tries fact-checked English condensation followed by Bodhan retranslation and synthesis.

The live-validation fixes add bounded recovery for unusable source text, budgeted source-boundary corroboration, fail-fast handling of blocked source, and one targeted retry for partial translation documents before speech. See Feature 23 in the walkthrough.

## Validation and demo status

**512 recipe tests passed locally and 2 were skipped.** The suite includes a thirty-second smoke test and checks that one language can fail independently. The recipe validator and dependency consistency checks pass.

The demo uses the supplied mathematics lesson. All 36 narration groups rendered, with zero skips.

| Language | Groups rendered | Subtitle cues | Video duration | Narration loudness |
| --- | ---: | ---: | ---: | ---: |
| Hindi | 12 | 48 | 5:29 | −16.11 LUFS |
| Tamil | 12 | 53 | 5:29 | −16.15 LUFS |
| Bengali | 12 | 40 | 5:29 | −16.28 LUFS |

Playback speeds stayed between 1.0× and 1.2×. The report has character error rates for all 36 groups, with six warnings. The demo completed through hosted stages and automatic fitting continuations. Exact acoustic sync remains unverified.

[Open the Hindi, Tamil, and Bengali dubs, subtitles, and reports](https://drive.google.com/drive/folders/1w0TS-eCZH6u00KkhyyKf5epPGO1QRk8y?usp=share_link)

[Watch the demo recording (4:02)](https://drive.google.com/file/d/1S2th1qKBc0D8y3mh1jVgW1Pn-JsAigd0/view?usp=sharing)

Tests:

```sh
PATH="$PWD/.venv/bin:$PATH" python -m pytest tests -q
python ../../scripts/validate_recipe.py .
```

The optional real-speech detector fixture can be supplied with `BODHAN_TEST_SPEECH_WAV=/path/to/authorized/speech.wav`. Without it that test skips; synthetic timing/format tests and a thirty-second end-to-end smoke test use mocked hosted APIs and real FFmpeg. Tests cover unsupported input, uncertain billing, budget blocking, changed cache artifacts, overlap ownership, multilingual captions, temporary failures and language isolation.

The Cookbook's `make check` was attempted. Its root suite fails on the existing `examples/AndroidSpeak` recipe (missing required source/configuration files). This recipe is validated separately; unrelated examples were not modified.

### CER improvements on two Tamil clips

We kept the translated text identical and generated each clip in shorter pieces. We then compared the original and repaired back-transcripts against the same reference text. Character error rate (CER) counts character edits divided by the number of reference characters. Lower is better.

| Clip | Original CER | Repaired CER | Drop in percentage points |
| --- | ---: | ---: | ---: |
| Tamil 6 · adjacent angles | 55.0% | 28.1% | 26.9 |
| Tamil 10 · linear pair example | 52.3% | 35.2% | 17.1 |

Both clips still exceeded our 20% review threshold. Clip 10 recovered the missing ending. In clip 6, some terms remained unclear in the back-transcript. These figures describe the two selected repairs. [Repair evidence](docs/code_walkthrough.md#evidence-and-result)

### How retries work

| What happens | What the pipeline does |
| --- | --- |
| A Bodhan request hits a rate limit or receives a server error | Waits before retrying. The main transcription, translation and speech clients allow up to three attempts in total, including the first. |
| A paid request times out after it may have reached Bodhan | Records the uncertain outcome and stops automatic resubmission of that request to avoid a duplicate charge. |
| A translation response is missing a segment | Keeps the valid segments and requests the missing or invalid IDs once more before speech generation. |
| A later stage needs a result that already succeeded | Reuses the matching cached result. A speech cache hit records zero new attempts and zero new estimated cost. |

## Trade-offs and remaining quality work

- Source word timestamps are unavailable. Placement and subtitle timing are estimates; ±300 ms sync is **not verified**. Physical fit is not a guarantee of natural phrasing or precise lip sync.
- Splitting long synthesis into bounded pieces reduces cutoff risk, but can affect prosody. Source pauses may occur only between independently generated complete phrase groups. If a complete group cannot fit, recovery or an explicit failure is required; low-energy waveform cuts are prohibited.
- Back-transcription CER is reported, including script/English-label disagreements. High CER does not prove the generated speech is wrong. Low CER does not prove correct translation or natural speech.
- The eleven selected numerical, terminology and missing-instruction repairs passed automated checks. Native-speaker terminology, pronunciation and naturalness review remain open; a back-translation or low CER does not certify the entire translation.
- The original mixed audio is excluded completely. Gaps stay silent, preventing original English from leaking through VAD mistakes. Source music/effects are lost; a separate verified voice-free background track would require a future explicit integration.
- One consistent voice per language; no diarization or voice cloning. Outputs carry AI-dubbed MP4 metadata. No OCR or final web UI is included.
- The orchestration has mocked end-to-end coverage. The hosted demo produced partial results and completed through automatic fitting continuations. The updated command has not completed a fresh, uncached run.
- Container setup and offline/mocked execution are verified on Linux ARM64. Linux AMD64, a literal fresh clone of the final submitted commit, undisplayed production languages and runtime of a fresh hosted run remain unverified. Do not claim hosted targets passed from offline rendering or container test time.

The full command now checks translation meaning before synthesis: explicit contextual number wording, Bodhan back-translation, an English-only factual review and bounded English/Bodhan repairs. Verified glossary concept IDs may appear in checking copies only; delivered text and speech remain Bodhan output. See Feature 20 of the walkthrough.

## Cost and AI assistance

The estimated validation cost was ₹20.79, including failed attempts and recovery. That is about ₹1.26 per input minute per language across this validation. The final offline render took 18.4 seconds. I used Groq’s free tier for the English planner.

### Cost improvements from targeted repairs

When only two Tamil clips needed another pass, we reused the other 40. The first column gives the recorded estimate for processing all 42 original clips once. The second gives the additional spend for the two repairs. The difference estimates what selective repair saved compared with repeating the original full pass at the same rates.

| Operation | Full pass · 42 clips | Repair · 2 clips | Estimated spend avoided |
| --- | ---: | ---: | ---: |
| Generate speech | ₹5.4870 | ₹0.4002 | ₹5.0868 |
| Transcribe generated speech for verification | ₹1.5277 | ₹0.1060 | ₹1.4218 |
| **Total** | **₹7.0147** | **₹0.5062** | **₹6.5086** |

Amounts are calculated at published rates and rounded independently to four decimals. The comparison covers speech and its verification only. [Synthesis and verification evidence](docs/code_walkthrough.md#evidence-and-result)

Codex assisted with implementation, tests, API investigation and documentation. English planning was tested with Groq's OpenAI-compatible endpoint using `openai/gpt-oss-120b`; provider-specific reasoning effort is optional with a fallback. The author should use the walkthrough to explain each component at the debrief.
