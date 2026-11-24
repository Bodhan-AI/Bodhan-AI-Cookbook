# Bodhan IndicEval — Translation and Speech Quality Harness

> An automated evaluation framework for assessing Indic-language translation and speech quality using Bodhan AI models, deterministic checks, back-translation, and LLM-based evaluation.

**Bodhan models:** `indic-translate`, `indic-speak`, `indic-transcribe`  
**Languages demonstrated:** Hindi, Tamil, Bengali  
**Language support:** Designed to support the 13 Indic-Speak production-tier languages through configurable language codes.  
**Format:** Python CLI application

## Overview

Bodhan IndicEval evaluates the quality of machine-generated translations and speech outputs for Indic languages.

It accepts JSONL datasets containing source text, language codes, optional reference translations, generated translations, and audio files. It evaluates translation quality through deterministic checks, back-translation, and an LLM judge. For speech, it evaluates audio characteristics and compares Bodhan Indic-Transcribe output against the expected text.

The framework produces structured evaluation reports, identifies potential errors, supports human-label calibration, and tracks estimated API usage and costs.

## Key Features

### 1. Translation Quality Evaluation
- Empty translation detection.
- Number preservation.
- Named entity preservation heuristics.
- Markdown and LaTeX integrity checks.
- Repetition detection.
- Length anomaly detection.
- Omission and addition checks for English text.
- Back-translation similarity using Bodhan Indic-Translate.

### 2. Speech Quality Evaluation
- Audio file validation.
- Duration checks.
- Silence detection.
- Clipping detection.
- Transcription comparison using Indic-Transcribe.
- Repetition detection in transcribed speech.

### 3. LLM-as-a-Judge
Uses an external LLM endpoint as a companion evaluation model.

The judge evaluates translation quality using a structured rubric and identifies:
- Meaning errors
- Number errors
- Omissions
- Additions
- Repetitions
- Fluency and grammar issues
- Terminology issues
- Formatting errors

Each identified error includes a category, severity, explanation, and text span.

**Important:** The LLM judge evaluates existing translations. It does not generate, rewrite, or correct translations.

### 4. Error Injection
Programmatically introduces controlled translation errors to create labelled evaluation examples.

This supports testing the evaluation pipeline against known error categories.

### 5. Human-Label Calibration
Compares automated predictions with human-labelled examples.

Reports include:
- Accuracy
- Precision
- Recall
- F1-score
- Per-category metrics
- Binary error-detection metrics
- Multi-label evaluation
- Threshold tuning

### 6. API Usage and Cost Tracking
Tracks:
- Bodhan translation token usage.
- Speech generation character usage.
- Transcription audio duration.
- Cache hits.
- Estimated API costs.
- LLM token usage and estimated costs.
- Partial cost information when pricing or provider usage data is unavailable.

A configurable `--max-cost-inr` limit protects Bodhan API calls. It does not enforce a combined budget across external LLM providers.

## Architecture

```text
                 JSONL Dataset
                       |
                       v
              Dataset Loader
                       |
                       v
              Translation Evaluator
                       |
          +------------+-------------+
          |            |             |
          v            v             v
      Deterministic  Back-         LLM Judge
        Checks      Translation   (Groq)
          |            |             |
          +------------+-------------+
                       |
                       v
                Speech Evaluator
                       |
             +---------+---------+
             |                   |
             v                   v
         Indic-Speak       Indic-Transcribe
          (Bodhan)             (Bodhan)
             |                   |
             +---------+---------+
                       |
                       v
                Result Aggregation
                       |
             +---------+----------+
             |                    |
             v                    v
       Human Calibration     Cost Tracking
             |                    |
             +---------+----------+
                       |
                       v
                Evaluation Reports
```

## Project Structure

```text
translation_speech_quality_harness/
|
|-- bodhan_eval/
|   |-- __init__.py
|   |-- __main__.py
|   |-- bodhan_client.py
|   |-- translation_checks.py
|   |-- back_translation.py
|   |-- speech_checks.py
|   |-- llm_judge.py
|   |-- error_injector.py
|   |-- calibration.py
|   |-- evaluator.py
|
|-- sample_data/
|-- outputs/
|
|-- .env.example
|-- .gitignore
|-- requirements.txt
|-- README.md
|
|-- test_translation_checks.py
|-- test_back_translation.py
|-- test_speech_checks.py
|-- test_error_injector.py
|-- test_calibration.py
```

## Requirements

- Python 3.10 or later.
- Bodhan AI API credentials.
- Groq-compatible LLM API credentials for LLM judging.
- Internet connectivity for live model requests.

## Setup

Clone the repository and navigate to the project directory:

```bash
cd examples/translation_speech_quality_harness
```

Create and activate a virtual environment.

**Windows PowerShell:**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Linux/macOS:**

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create your environment file:

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

**Linux/macOS:**

```bash
cp .env.example .env
```

Configure the following environment variables in `.env`:

```env
BODHAN_BASE_URL=https://api.bodhan.ai

BODHAN_TRANSLATE_KEY=your_translate_key
BODHAN_SPEAK_KEY=your_speak_key
BODHAN_TRANSCRIBE_KEY=your_transcribe_key

LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=openai/gpt-oss-20b
LLM_API_KEY=your_llm_api_key
```

Optional pricing configuration:

```env
BODHAN_TRANSLATE_INPUT_INR_PER_1K_TOKENS=
BODHAN_TRANSLATE_OUTPUT_INR_PER_1K_TOKENS=
BODHAN_SPEAK_INR_PER_10K_CHARS=
BODHAN_TRANSCRIBE_INR_PER_MINUTE=

LLM_INPUT_INR_PER_1M_TOKENS=
LLM_OUTPUT_INR_PER_1M_TOKENS=
```

Obtain Bodhan credentials from the [Bodhan AI Console](https://console.bodhan.ai).

Never commit `.env` or expose API credentials.

## Running the Evaluation

The main CLI interface is:

```bash
python -m bodhan_eval \
    --dataset path/to/data.jsonl \
    --langs hi,ta,bn \
    --out ./report
```

### Generate Missing Outputs

Use `--generate` to generate missing translations and speech outputs through Bodhan models:

```bash
python -m bodhan_eval \
    --dataset sample_data/sample.jsonl \
    --langs hi,ta,bn \
    --out ./report \
    --generate
```

Without `--generate`, the evaluator scores the provided outputs rather than generating missing ones.

### Set an API Budget

```bash
python -m bodhan_eval \
    --dataset sample_data/sample.jsonl \
    --langs hi \
    --out ./report \
    --max-cost-inr 50
```

The budget applies to Bodhan API calls, not external LLM usage.

### Compare Against a Baseline

```bash
python -m bodhan_eval \
    --dataset sample_data/sample.jsonl \
    --langs hi,ta,bn \
    --out ./new_report \
    --baseline ./previous_report
```

### Run Human-Label Calibration

```bash
python -m bodhan_eval \
    --dataset sample_data/sample.jsonl \
    --langs hi \
    --out ./report \
    --calibration sample_data/labels.jsonl
```

### CLI Arguments

| Argument | Description |
|---|---|
| `--dataset` | Input JSONL dataset |
| `--langs` | Comma-separated language codes |
| `--out` | Output report directory |
| `--generate` | Generate missing translations and speech |
| `--baseline` | Previous report for comparison |
| `--calibration` | Human-labelled JSONL dataset |
| `--max-cost-inr` | Maximum Bodhan API budget in INR |

## Input Dataset Format

Each JSONL record contains at least an `id`, `source`, and `lang` field.

Example:

```json
{
  "id": "hi_001",
  "source": "भारत की राजधानी नई दिल्ली है।",
  "lang": "hi",
  "reference": "The capital of India is New Delhi.",
  "translation": "The capital of India is New Delhi."
}
```

Optional fields can include:
- `audio`: Path to an existing audio file.
- `translation`: Existing generated translation.
- `reference`: Reference translation.
- Human-labelled error categories.

Each record must be stored as a separate JSON object on its own line.

## Output Reports

The evaluator generates the following files:

| File | Description |
|---|---|
| `items.jsonl` | Per-item evaluation results, checks, errors, and evidence |
| `summary.json` | Structured evaluation summary and usage statistics |
| `summary.md` | Human-readable evaluation report |
| `calibration.json` | Human-label calibration metrics |
| `diff.md` | Comparison against a previous baseline |

## Testing

Run the standalone offline component tests:

```bash
python test_translation_checks.py
python test_back_translation.py
python test_speech_checks.py
python test_error_injector.py
python test_calibration.py
python test_calibration_integration.py
python test_combined_cost_report.py
python test_bodhan_client.py
```

These tests cover deterministic checks, mock-based API behaviour, error injection, calibration, and cost reporting.

Live API smoke tests are separate and may consume API credits.

## Limitations and Design Considerations

- Deterministic translation checks are heuristic signals and cannot fully establish semantic equivalence.
- Back-translation similarity is a quality signal, not proof of translation correctness.
- Named entity and lexical omission checks have language-dependent limitations.
- Speech silence and clipping thresholds are configurable heuristics.
- Human-label calibration quality depends on the size and representativeness of the labelled dataset.
- Cost estimates depend on configured pricing and available provider usage metadata.
- The Bodhan API budget does not cap external LLM charges.
- The LLM judge evaluates translations but does not generate or modify them.

## Design Philosophy

The framework combines deterministic checks, model-assisted evaluation, speech analysis, and human-label calibration rather than relying on a single quality metric.

This hybrid approach makes evaluation more explainable by providing error categories, severity levels, evidence, and per-item results alongside aggregate metrics.

The design also emphasizes reproducibility through caching, retry handling, structured outputs, and configurable API budgets.