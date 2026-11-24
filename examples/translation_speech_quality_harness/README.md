# Translation & speech quality harness

> Finds and explains quality problems in Indic-Translate translations and Indic-Speak audio: every flag says what is wrong, where (exact span), how bad, and which checks agreed.

**Bodhan models:** indic-translate, indic-speak, indic-transcribe
**Languages shown:** Hindi, Tamil, Bengali (any Indic-Speak language works through `--langs`)
**Format:** CLI (`python -m bodhan_eval`) + a short walkthrough notebook

## What it does

It reads a JSONL dataset of English sources with optional reference translations, system outputs (text or audio) and human labels.
It scores the translations and speech, and, with `--generate`, creates missing outputs with Bodhan models. Cheap deterministic checks
run first (script, numbers, Markdown/LaTeX, entities, length), then model round trips (back-translation, back-transcription), then an
LLM judge with a written rubric. A focused LLM verifier looks only at the cases near the decision threshold. Signals are fused into
flags with calibrated per-category thresholds, and the harness measures itself against labelled items (precision and recall per
category, cross-validated). The report gives per-item flags with evidence, per-language and per-category summaries, cost, and a
diff against a baseline run.

**Demo video (4:47):** https://youtu.be/6hEUReNo7Pk

Docs: [design notes (architecture, flow chart, every check, judge rubric, calibration)](docs/DESIGN.md) · [decision log](docs/DECISIONS.md) · [judge rubric](src/bodhan_eval/judge/rubric.md) · [sample report](sample_report/summary.md)

## Setup

Python 3.10+ (tested on 3.11). `ffmpeg` is needed only for non-WAV audio inputs.

```bash
cd examples/translation_speech_quality_harness
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt && pip install -e . --no-deps
cp .env.example .env   # three Bodhan keys (one per model) + LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
```

Or with Docker: `docker build -t bodhan-eval . && docker run --env-file .env -v "$PWD/report:/app/report" bodhan-eval --dataset data/demo.jsonl --langs hi,ta,bn --out report`

## Run

```bash
python -m bodhan_eval \
  --dataset data/demo.jsonl \
  --langs hi,ta,bn \
  --out ./report \
  [--generate] [--baseline ./previous_report] [--calibration labels.jsonl] [--max-cost-inr 50]
```

Useful extras: `--no-llm` (deterministic and round-trip checks only), `--speech-sample N` (cap TTS with `--generate`),
`--consistency-sample N` (re-judge N items to measure judge consistency; default 30), `--fail-on-regression N`
(CI mode: exit 3 if more than N new major/critical flags vs `--baseline`), `--thresholds my.json`, `--freeze-thresholds`
(measure but do not re-tune; automatic with `--baseline`, so both runs are judged by the same thresholds), `--cache-dir`, `-v`.

Tests (no network): `pytest -q`. Label the review queue and feed it back: `python -m bodhan_eval.review --csv report/review_queue.csv --annotator <you> --out labels.jsonl`, then rerun with `--calibration labels.jsonl`.
Make a second system version for a diff demo: `python -m bodhan_eval.inject.variant --in data/demo.jsonl --out data/demo_v2.jsonl`. Rebuild the demo set: `python -m bodhan_eval.inject --sources data/sources.jsonl --langs hi,ta,bn --out data/demo.jsonl --max-cost-inr 9`.

**Dataset format:** one JSON object per line. Only `id`, `source` and `lang` are required; `reference`, `translation`, `audio` (path relative to the
dataset file) and `labels` (`[{"type", "severity", "span": [start, end]}]`, spans as character offsets into `translation`) are optional.
`"labels": []` means verified clean; leaving `labels` out means unlabelled. Label types are matched through aliases
(`truncation`, `term_or_entity`, `untranslated`, …; see `taxonomy.py`).

**Output (`--out`):**

| File | Contents |
|---|---|
| `summary.md` | headline numbers, quality vs targets, per-language (with MQM-style score) and per-category tables, top failures with evidence, regressions |
| `summary.json` | the same metrics, machine-readable |
| `items.jsonl` | per item: checks run, flags (category, severity, span, flagged text, source span, confidence, evidence from each check), back-translation, ASR text, notes |
| `calibration.json` | checks and judge vs labels: precision/recall per category and per check, tuned thresholds and why, cross-validated metrics, judge consistency |
| `diff.md` / `diff.json` | with `--baseline`: what improved, what regressed, by category and language, with examples |
| `review_queue.csv` | uncertain items to label by hand; convert with `python -m bodhan_eval.review` and feed back with `--calibration` |
| `dashboard.html` | static page with filters by language, category and severity |
| `logs.jsonl`, `run_meta.json` | structured per-check/per-item logs; actual spend, cache hits, wall time |

## How it works

1. **Load:** malformed lines are logged, counted and skipped; the run never stops on a bad item.
2. **Free checks:** wrong script/language, number and unit preservation (after Indic digit normalization), Markdown/table/LaTeX integrity,
   names and terms, and a length ratio against a baseline learned from the dataset itself.
3. **Back-translation** (Indic-Translate → English): re-checks numbers, units and names in English, measures sentence coverage, and is shown to the judge.
4. **LLM judge** (MQM-style rubric): sees the translation as numbered tokens and answers with token ranges, so spans are exact and the LLM never writes Indic text.
5. **Speech:** audio signal checks (silence, noise, clipping, speech rate), then Indic-Transcribe in ≤25 s chunks, then a fuzzy,
   number-aware alignment that looks for *runs* of missing or repeated words, which separates TTS failures from ASR noise.
6. **Fusion:** overlapping signals of a category form one suspected error, scored with a weighted noisy-OR. Noisy checkers never flag alone.
7. **Verifier:** only clusters near the threshold get a focused LLM second look (confirm or deny).
8. **Calibration:** thresholds are tuned per category on labelled items (false alarms ≤10%), with grouped cross-validation.
9. **Report**, plus a diff against `--baseline`.

Every model output is cached by its exact input, so reruns are free and byte-identical.

## Results on the demo set

Demo set: 46 original English sources × Hindi, Tamil and Bengali = **260 items**: 138 clean, 76 with injected translation errors,
46 with injected speech errors (92 items have audio). Built with `python -m bodhan_eval.inject` (seed 7); see `data/demo.manifest.json`.
Judge: `openai/gpt-oss-120b` (Groq, reasoning effort low). Sample report: [`sample_report/`](sample_report/) (open `summary.md` or `dashboard.html`).

Calibration labels: the injector's labels, plus **10 real Indic-Translate errors that the harness found in "clean" outputs**. They came
out of `review_queue.csv`, were reviewed with AI assistance and verified mechanically against the text (the `###`/`**` markers or the
name are absent from the translation), and are listed in `data/labels_review.jsonl` with `"annotator": "ours: ai-assisted review"`.
The run uses `--calibration data/labels_review.jsonl`.

| Target | Goal | Tuned on all labels | **5-fold grouped CV** | CV with assumed-clean labels only |
|---|---|---|---|---|
| Detection recall | ≥ 80% | 97% | **96%** ✅ | 93% |
| False alarms on clean items | ≤ 10% | 3.9% | **7.8%** ✅ | 12.3% (counts the 10 real errors as false alarms) |
| Localization (span overlap) | ≥ 70% | 92% | **92%** ✅ | 96% |
| Category accuracy | ≥ 70% | 100% | **100%** ✅ | 100% |
| Judge consistency (same verdict on re-judge, 30 items) | ≥ 90% | **100%** ✅ (same category set: 87%) | | |
| Determinism | identical cached rerun | ✅ byte-identical at ₹0 (`tests/test_e2e.py`); the demo reruns from cache in seconds | | |

Per language (tuned): recall bn 93% / hi 100% / ta 98%; false alarms bn 4.7% / hi 2.3% / ta 4.8%.

Per category (tuned; precision / recall): number 100/100, wrong language 100/100, omission 100/100, addition 92/100, format 100/95,
term 80/80, mistranslation 37/100, speech omission 96/96, speech repetition 100/100, speech garbled 100/100.
An unseen language (Telugu) runs with `--generate` and no code changes: [`sample_report/unseen_te/`](sample_report/unseen_te/summary.md).
The baseline diff of a simulated second system version ([`sample_report/v2_vs_v1/diff.md`](sample_report/v2_vs_v1/diff.md)) caught 15 of 16 introduced regressions, showed all 15 fixes as clean, and changed nothing on unchanged items.

**Where we miss and why**
- *Mistranslation precision (37%)* is the weak spot. The LLM judge reports "mistranslation" on correct but less common word choices
  (for example the standard Tamil words for "syntax" and "space", and the idiomatic Hindi `बस छूटती है` for "the bus leaves"), and on items
  whose real error belongs to a different category. Recall is high because the judge is generous; precision is what a stronger judge model would fix.
- *Term* (80/80): transliteration matching misses some heavily adapted name forms, and the judge sometimes calls a name swap a mistranslation.
- *Localization (92%)*: a dropped heading or bold marker has no span in the translation (the text is simply missing), so those flags point at
  the source instead.
- The demo surfaced **real Indic-Translate errors** in unreviewed output: `###` heading markers dropped (md-02, md-07 in all three languages),
  `**bold**` dropped (md-06 hi/ta), a Markdown table's pipes and separator row lost (md-06 bn), and a surname dropped (ent-06 ta).

**Cost and runtime**

| | Per 100 items, without speech | Per 100 items, with speech checks on supplied audio | Per 100 items, generating speech too |
|---|---|---|---|
| Bodhan cost | ≈ ₹0.05 (back-translation) | ≈ ₹1.6 (adds ~16 min of audio for Indic-Transcribe) | ≈ ₹8 (adds Indic-Speak at ~₹6.75 per 100 two-sentence clips) |
| LLM tokens | ≈ 135k prompt + 24k completion (judge, verifier on uncertain items, consistency sample) | same | same |

Runtime is dominated by provider limits, not compute. From cache, the 260-item demo reruns in about 60 s. Uncached, the first run took about
45 min, because of the free Groq tier (8k tokens/min) and Bodhan's model-wide throttle (below). Each Bodhan call itself takes
0.7–1.5 s (translate, transcribe) or about 10 s (speak). With unthrottled keys and `--concurrency 8`, 200 items in 3 languages with speech
checks fit well inside 30 minutes.

## Notes from building against the hosted API (November 2026)

- `POST /translate` returned 404. Translation works through `POST /v1/chat/completions` with `model="indic-translate"`,
  `target_language`, and `max_output_tokens`; the client falls back to `/translate` if that route comes back. Without
  `target_language` the model picks a language itself.
- `/v1/audio/speech` returns WAV bytes labelled `audio/mpeg`, so the client sniffs the bytes.
- Indic-Transcribe writes numbers as spoken words, often English words in the native script (Hindi `हंड्रेड` for "100").
- Translate keys allow 120 requests per minute and 10 in parallel, and there is also a model-level throttle
  (`429 Maximum rate limit reached for the model`) with no `retry-after`. The client backs off for up to about 2.5 minutes.
  On 20–21 Nov this throttle slowed dataset generation noticeably.

## Limitations

- **Subtle mistranslations** rely on the LLM judge reading Indic text. The judge is measured (precision/recall per category,
  re-judge consistency) and corroborated, but on this set it is the weakest link (judge-only mistranslation precision 27%).
  A stronger judge model would help most.
- **Same-script wrong language** (Hindi/Marathi/Nepali, Bengali/Assamese) uses small function-word, character and suffix profiles
  in `langs.py`. Other same-script pairs (for example Maithili/Hindi) have no profile and fall back to the judge.
- **Garbled-audio localization is approximate:** ASR returns no timestamps, so a noise burst's time is mapped to words by
  proportion (marked `span_is_approximate`). Omission, truncation and repetition are localized through the text alignment.
- **Speech checks compare against the spoken text.** Math, code and URLs are not spoken, so speech errors inside them are not
  checked. Audio longer than 25 s is split at the quietest point near each boundary.
- **Back-translation and ASR noise** are handled by corroboration and structure, not removed. A real omission that both the
  judge and the back-translation miss, and that leaves the length unchanged, will be missed.
- **Term consistency across items** (the same term rendered differently in different items) is not implemented, only
  per-item preservation.
- Thresholds shipped in `thresholds.json` come from calibrating on this demo set. With `--calibration`, categories with ≥6
  labelled examples are re-tuned on the supplied labels; the others keep these defaults.
- Indic-Transcribe Core vs Flex cannot be selected on the hosted API (one model id, `indic-transcribe`), so a second-ASR
  agreement check was not possible.

## AI assistance

Built with Claude Code as a pair programmer: architecture discussion, code generation that I reviewed, tests, and documentation drafts. The API spike, design decisions (see the decision log) and demo-data choices were made together; I can walk through every module.

## Data and licences

`data/sources.jsonl` is original English text written for this recipe. Translations and audio are Indic-Translate/Indic-Speak outputs.
Generated audio is not committed (`data/audio/` is git-ignored); rebuild it with the inject command above (about ₹3.3 of Indic-Speak) or download it: [demo_audio.zip](https://github.com/23f2001033/Bodhan-AI-Cookbook/releases/download/quality-harness-demo-audio/demo_audio.zip) (92 WAV files, unzip into `data/audio/`).
All labels in `data/demo.jsonl` are produced by the injector (`"annotator": "injector"`); clean items are *assumed* clean
(`"label_source": "assumed clean: unreviewed Indic-Translate output"`). Labels we added beyond the injector are kept separately in `data/labels_review.jsonl`
(10 real errors from the review queue, AI-assisted review, mechanically verified) and carry `"annotator": "ours: ai-assisted review"`.
No other human annotation was added.
