
## Summary

An evaluation harness that scores Bodhan Indic-Translate and Indic-Speak output, flags
specific errors with a character span and evidence, calibrates its own thresholds against
human labels, and reports what changed against a baseline. It runs unattended on an unseen
dataset; nothing is special-cased to the demo data.

## How to run

```bash
git clone <this branch>
cd examples/bodhan-quality-harness
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env          # BODHAN_* keys + an OpenAI-compatible judge

python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out ./report
python -m bodhan_eval --dataset data/demo.jsonl --langs hi,ta,bn --out ./report --baseline ./previous_report
pytest -q
```

## Architecture

See `README.md` for the Mermaid diagrams. One line per check:

- `script.wrong_script` — fraction of characters in the language's own script, computed on
  prose only so Markdown tables do not trip it.
- `script.untranslated` — output identical to the English source.
- `numbers.added_or_changed` — a significant number in the output absent from the source;
  localizes exactly on the wrong digit.
- `numbers.missing` — a significant source number absent from the output.
- `format.*` — heading, table-row, code-fence and LaTeX command counts must survive.
- `length.*` — word-ratio beyond swept thresholds, guarded below 8 source words.
- `length.repeat` — repeated 6-word shingle inside the translation; span from the second
  occurrence.
- `terms.entity_missing` — acronyms and technical tokens must survive; proper names are
  excluded because they are transliterated, not preserved.
- `reference.chrf` — dependency-free chrF when a reference exists.
- `speech.signal` — duration, silence fraction, clipping and RMS read from the WAV.
- `speech.roundtrip` — Indic-Transcribe back-transcription compared to the expected text.

Flags are deduplicated before scoring: two checks catching one defect are merged rather
than counted twice.

## Harness design

Tier 0–1 run on every item and cost nothing. Tier 2 (judge) runs only on items the cheap
tiers flagged, plus an optional sample. Tier 3a (speech signal) runs on every item with
audio; tier 3b (paid round-trip) runs only on `--speech-sample` items. `--max-cost-inr`
stops the run before it overspends, and every model output is cached by exact input.

## Where the LLM is used, and why

Only as the judge. It receives the source, the translation and the flags already found, and
returns category, severity, character span and a one-sentence explanation. It never writes
or corrects Indian-language text. Temperature 0, cached, and `judge_stats` records how many
items were judged so consistency can be measured. Mistranslation recall (0.14 without the
judge) is the gap the judge exists to close.

## Bodhan models used

- Indic-Translate — translations and back-translations (hosted API).
- Indic-Speak — speech generation (hosted API).
- Indic-Transcribe (Core) — back-transcription for speech checks (hosted API).

No other speech, translation or TTS system is called by the harness. Endpoint paths are
OpenAI-compatible and overridable by env var.

## Results

On the 108-item demo set generated with Bodhan Indic-Translate across hi/ta/bn, with the LLM judge enabled:

| Metric       | Value | Target |
| ------------ | ----- | ------ |
| Recall       | 93.5% | ≥ 80%  |
| False alarms | 3.5%  | ≤ 10%  |
| Precision    | 74.1% | —      |
| F1 Score     | 0.827 | —      |
| Localization | 100%  | ≥ 70%  |

Per category (Precision / Recall): addition 0.73/0.96, language 0.92/1.00, number 0.78/0.88.
Judge tier active. Reruns are byte-identical with digest `4535583a6a99fd13`.

## Sample Report

Pre-generated evaluation artifacts on the 108-item Bodhan Indic-Translate demo set:

- **[Markdown Summary Report](docs/sample_report/summary.md)**
- **[Interactive HTML Report](docs/sample_report/index.html)**
- **[Evaluation Item Breakdown (JSONL)](docs/sample_report/items.jsonl)**
- **[Summary Statistics (JSON)](docs/sample_report/summary.json)**

## Demo video

Five minutes or less — a full run on the demo dataset, a walk through the report, and the
optional Control Center UI:

**https://drive.google.com/file/d/1I_OxB5BFGrs01RDszR1E_Vkveair0oJl/view**

## Stretch goals implemented

- **Interactive Web Control Center (`--serve`)**: A real-time web application to translate live with Bodhan models, inspect quality scores, and trigger dataset runs directly in the browser.
- **Human Review Queue**: View uncertain and flagged items, annotate labels, and export as CSV (`/api/export-review-queue`).
- **Dynamic Threshold Calibration**: Sweeps and updates physical length thresholds in real time from labelled examples (`/api/calibrate`).
- **MQM Quality Scoring**: Severity-weighted quality penalty metric computed per item and aggregated per language.

## Trade-offs and known limitations

Mistranslation recall depends on the judge. Omission localization is approximate because a
dropped clause has no position in the output. Length ratio is noise on very short items.
Speech checks require PCM WAV. Transliterated entity errors are not yet detected.

## Cost and runtime

Per 100 items: ~₹0.25 translation only, ~₹12 with speech synthesis, ~₹3.3 with
back-transcription — about ₹15 for a full run. Cached reruns cost nothing.

## AI assistance

Built with an AI coding assistant. The false-alarm controls (prose-only script checks,
excluding proper names from the entity check, refusing to sweep thresholds on small
samples) each came from measuring the harness against injected errors and fixing a specific
over-firing check.
