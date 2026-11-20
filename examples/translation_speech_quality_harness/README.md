# Translation and Speech Quality Harness

Finds, locates and explains errors in Indic-Translate translations and Indic-Speak audio, with evidence for every flag.

**Bodhan models:** Indic-Translate, Indic-Speak, Indic-Transcribe
**Languages shown:** Hindi, Tamil, Bengali (any Indic-Speak production language works; also tested on Telugu, Urdu and Marathi)
**Format:** CLI

## What it does

The harness reads a JSONL dataset of English sources with their translations and audio. It runs cheap deterministic checks first, then Bodhan round trips, and asks an LLM judge only about meaning. The report says what is wrong, where, and which checks agree, so a model team can act on it without re-reading the data.

From `report_demo/summary.md`:

> - **wmt-125 / bn**: wrong_language, critical, confidence 0.99, found by judge, regen_diff, script
>   - source: "state prisons"

Links:
- Sample report: [`report_demo/summary.md`](report_demo/summary.md), with every file in [`report_demo/`](report_demo/) and a filterable [`dashboard.html`](report_demo/dashboard.html)
- Baseline diff: [`report_demo_v2/diff.md`](report_demo_v2/diff.md)
- Design notes, [architecture diagram](DESIGN.md#architecture) and [flow chart](DESIGN.md#flow-from-dataset-to-report): [`DESIGN.md`](DESIGN.md)
- Demo data and attribution: [`data/README.md`](data/README.md)
- Demo video (2 min 45 s): [quality_harness_demo.mp4](https://github.com/Achindra2003/Bodhan-AI-Cookbook/releases/download/quality-harness-demo-assets/quality_harness_demo.mp4)

## Setup

Requires Python 3.10+ and `ffmpeg` on `PATH`.

```bash
git clone https://github.com/Bodhan-AI/Bodhan-AI-Cookbook.git
cd Bodhan-AI-Cookbook/examples/translation_speech_quality_harness
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in the keys
```

Or with Docker, which brings its own ffmpeg:

```bash
docker build -t bodhan-eval .
docker run --rm --env-file .env -v "$PWD/report:/app/report" bodhan-eval \
  --dataset data/demo.jsonl --langs hi,ta,bn --out report
```

Settings come from environment variables: `BODHAN_TRANSLATE_KEY`, `BODHAN_SPEAK_KEY`, `BODHAN_TRANSCRIBE_KEY`, and `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` for any OpenAI-compatible endpoint. `--env-file .env` loads them from a file; variables already set win.

## Run

```bash
python -m bodhan_eval \
  --dataset path/to/data.jsonl \
  --langs hi,ta,bn \
  --out ./report \
  [--generate] [--baseline ./previous_report] [--calibration labels.jsonl] [--max-cost-inr 50]
```

The demo, and the baseline diff against it:

```bash
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out report_demo --env-file .env
python -m bodhan_eval --dataset data/demo_v2.jsonl --langs hi,ta,bn --out report_demo_v2 \
  --baseline report_demo --fail-on-regression 5 --env-file .env
```

Extra flags:

| Flag | What it does |
| --- | --- |
| `--fail-on-regression N` | With `--baseline`: exit 1 when more than N items gain a major or critical flag (CI mode) |
| `--skip CHECKS` | Switch off checks for comparison runs: `numbers`, `format`, `script`, `back_translation`, `regen_diff`, `audio_stats`, `transcript`, `judge` |
| `--cache-dir`, `--concurrency` | Where model outputs are cached (default `.bodhan_eval_cache/`); parallel calls per model (default 4) |
| `--calibration` | Human labels as JSONL, or a filled-in `review_queue.csv` from an earlier report |

The error injector plants labelled errors into clean items:

```bash
python -m bodhan_eval.inject --dataset clean.jsonl --out injected.jsonl --rate 0.5 --seed 7 --env-file .env
```

Voice comparison speaks the same translations with each Indic-Speak voice and scores every recording with the same speech checks:

```bash
python -m bodhan_eval.voices --dataset data/demo.jsonl --langs hi,ta,bn --out report_voices --per-lang 8 --env-file .env
```

Tools:
- `tools/holdout.py` calibrates on half the items and scores the other half.
- `tools/build_unseen.py` builds an injected test set in languages the demo does not use.
- `tools/cost_runtime.py report_demo` prints cost and runtime per 100 items.

Run the tests with `pytest` (239 tests, offline, about 12 seconds). A run prints one progress line per stage on stderr.

Report files: `summary.md`, `summary.json`, `items.jsonl`, `calibration.json`, `diff.md` (with `--baseline`), plus `review_queue.csv`, `dashboard.html` and `run_log.jsonl`. Every flag in `items.jsonl` lists its category, severity, spans on both sides, quotes, and the evidence from each check. `run_log.jsonl` records every model call with its cache key, so any flag can be traced back to the calls behind it.

## How it works

There are three tiers. Each check emits candidates with a strength: exact, noisy or weak. One combiner decides what becomes a flag. A single noisy signal never flags alone.

| Tier | Check | Catches | Strength |
| --- | --- | --- | --- |
| 1, code | Numbers and units | Changed digits, units, currency; native digits, lakh and crore handled | Exact when changed, noisy when missing |
| 1, code | Format | Markdown headings, lists, tables, LaTeX, code, URLs, HTML | Exact |
| 1, code | Script | Wrong script, foreign words, English left untranslated | Exact |
| 1, code | Length, reference | Length outliers within the language; chrF++ and BLEU when a reference exists | Weak, noisy |
| 2, Bodhan | Back-translation | Source sentences missing from the round trip, extra sentences | Noisy; re-checked sentence by sentence |
| 2, Bodhan | Regeneration diff | Words that differ from a fresh greedy translation | Exact if this run reproduces most items, else noisy |
| 2, code | Audio statistics | Silence, clipping, long pauses, noise, speaking rate, abrupt end | Exact to weak |
| 2, code | Sound alone | Half a second or more of noise instead of speech; a stretch that is a copy of the one before it | Exact |
| 2, Bodhan | Back-transcription | Missing tail, skipped words, repeated or looped phrases, garbled stretches | By shape of the mismatch, never by error rate. A suspected skip or repeat is re-transcribed on its own as a second signal |
| 3, LLM | Judge | Mistranslation, omission, addition, term, with quotes | Two passes; counts only if both agree and every quote is found in the text. Flags only with another check beside it; alone, it goes to the review queue |

The LLM never writes Indian-language text. It quotes, code finds the quote, and Indian-script characters are stripped from its explanations. Thresholds come from statistics of the current run and from the labels you pass in, never from constants tuned on the demo. Details are in [`DESIGN.md`](DESIGN.md).

## Results on the demo set

The demo set has 120 rows: 40 sources in Hindi, Tamil and Bengali, with 60 injected errors (2 per category per language) and 60 clean rows. Judge: `openai/gpt-oss-120b`. The holdout calibrates on half A (42 rows) and scores half B (78 rows), split by a hash of the id.

| Run | Recall (≥ 80%) | False alarms (≤ 10%) | Localization (≥ 70%) | Category acc. (≥ 70%) |
| --- | --- | --- | --- | --- |
| Full demo, all 120 rows | **100%** | **0%** | **98%** | **95%** |
| Half B, all checks | **100%** | **3%** | **100%** | **95%** |
| Half B, judge off | 76% | **3%** | 77% | **94%** |
| Half B, regeneration diff off | 76% | **3%** | 77% | **97%** |
| `demo_v2` (other injected errors, seed 11) | **98%** | **2%** | **93%** | **96%** |
| **Unseen languages: Urdu and Marathi**, smaller judge (40 rows) | **100%** | **0%** | **95%** | **95%** |

- Judge consistency is 95%, against a 90% target: re-judging gave the same verdict.
- Determinism: a rerun from cache gives byte-identical report files at no cost.
- Per language on the full demo: recall is 100% in Hindi, Tamil and Bengali, and false alarms are 0% in every language.
- Per category, from `report_demo/summary.md`:
  - 100% on addition, format, garbled, omission, speech omission, speech repetition and wrong language.
  - 83% on mistranslation and number.
  - 67% on term.
- Unseen languages: [`report_unseen/`](report_unseen/summary.md) was built with `tools/build_unseen.py`. Urdu is right-to-left, with its own script and digits; Marathi shares Hindi's script. Nothing in the code changed for them. The judge was a smaller model, `qwen/qwen3.8-27b`.
- Voices: [`report_voices/voices.md`](report_voices/voices.md) compares the two Indic-Speak voices of each language on the same 8 texts.

**The judge flags only with support.** In an earlier version, a finding from the judge alone became a flag. On the full demo that gave 12 flags, none of them on an injected error, and a 15% false-alarm rate. Several were real model errors on rows labelled clean only because nothing was injected into them: "vegan" became शाकाहारी (vegetarian), and "Swimming Pool" became পুকুর (pond). Judge-only findings now go to `review_queue.csv`, where both of these examples are, so a person decides. The cost shows in the regen-diff-off row: without the diff, nothing else backs the judge on meaning errors, and recall falls to the judge-off level.

**Speech is checked by ear and by sound.**
- **The transcript.** A repeat or skip seen in the transcript may be the transcriber's own mistake, so it is one noisy signal. The harness transcribes just that stretch again, and hearing it again in that separate cut is the second signal.
- **The sound alone, with no model.**
  - Half a second or more of loud, spectrally flat sound is noise, not speech. The longest speech sound like it, an "s", lasts about 0.2 s.
  - A 0.3 s stretch that nearly matches the one just before it was copied, not spoken again. On the 80 demo recordings, clean speech scored at most 0.975 on this measure, and every copied stretch scored 1.0. The cut-off is 0.995.
- **What it catches and misses.** These detectors catch noise and copies however the transcriber hears them. A repeat that was synthesised again rather than copied, or quiet coloured noise, rests on the transcript alone.
- **The judge has no say on speech.** Its verdict does not count against speech findings, because it reads the text and never hears the audio.

## Cost and runtime

Measured from the cache with `tools/cost_runtime.py`, at hosted prices:

| | Per 100 items |
| --- | --- |
| With speech checks | ₹1.09 |
| Without speech checks | ₹0.22 |
| Generating audio too (`--generate`) | ₹9 to ₹20, depending on text length (measured: ₹4.17 for 45 short items, ₹1.00 for 5 long passages) |

**Runtime**, measured on 20 Nov 2026 from an empty cache: the full demo (120 items, with speech checks, judge off, concurrency 4) took **17.7 minutes**. It made 292 Bodhan calls and cost ₹1.28.
- **Model time is small.** The calls themselves took about 2 minutes: a median of 0.5 s per translation and 0.8 s per transcription.
- **Rate limits took the rest.** The rest was waiting on Bodhan rate limits for our key: 59 translation retries and 37 transcription retries, each honouring the server's `Retry-After`. One transcription still failed after 8 attempts. It is reported as `not_run`, and the run carried on.
- **200 items on our key:** about 30 minutes, plus the judge. **Without throttling:** about 4 minutes of model time.
- **The judge:** on a free-tier endpoint with an 8,000 tokens-per-minute limit, it adds most of the remaining time. Raise `--concurrency` on a paid endpoint.
- **Shared pause:** that run was measured before rate-limit pauses were shared between workers. Now one 429 pauses every worker on that model until `Retry-After`, so they no longer spend retries against the same closed window. A run prints one progress line per stage on stderr.
- **Reruns:** a rerun from cache takes seconds.

## Limitations

- Recall on injected errors is an upper bound for real errors, which are subtler.
- The regeneration diff finds deviations from what the model produces today, not errors it makes every time. Those rest on the judge.
- Languages that share a script (Hindi and Marathi) are told apart only by the judge.
- Subtle, fluent mistranslations depend on the judge model's skill in that language.
- Speech positions are approximate, because transcription returns no timestamps. Only silences have exact times.
- In-run statistics need at least 8 items per language. Smaller languages are pooled together.
- The cut-off that makes a missing audio tail exact evidence (a third of the text) was picked while looking at the demo data. It is the same for every language, and it caused no false alarm on half B.
- Hand labels cover only checks that need no reading of the language: numbers, format, script and English left in. See [`data/README.md`](data/README.md).
- A name swapped in for an ordinary word ("Dosage note" becoming "Pune note") is found, but categorised by the judge as a mistranslation rather than a term. Fixing it means changing the judge's rubric, which would redo every judge call.
- The sound-only detectors were checked against our injector's noise and splices. Other kinds of damage rest on the transcript.

## What I would do next

- Raise the judge's precision per language with a few labelled clean examples from each new language.
- Teach the rubric that a name appearing where the source has an ordinary word is a term error.
- Get word timestamps, if the API offers them, so speech errors have exact times.

## AI assistance

Claude Code (Claude Opus) was used for research, planning, writing code and tests, and running the evaluations. The design decisions and their reasons are in [`DESIGN.md`](DESIGN.md), and I can explain every line.
