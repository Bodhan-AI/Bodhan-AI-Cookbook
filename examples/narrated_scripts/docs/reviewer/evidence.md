# Demo reproduction and evidence

From the recipe directory, use the [README setup](../../README.md#setup)
and [model configuration](../../README.md#run). The example demo commands
default to local-first. Set `DEMO_BACKEND=hosted` to use the GPU-free setup
and service-specific credentials. Keep the same output path to resume a demo.
Use a new path when measuring a fresh run.

## Demo 1 NCERT Lines and Angles

Use the assigned English NCERT Class 9 chapter. Save the PDF locally and
keep it outside version control. The CLI rasterizes the PDF into pixels.

```bash
mkdir -p inputs .local
NCERT_PDF="$PWD/inputs/Lines-and-Angles-NCERT.pdf"
curl -fL 'https://www.aspirationsinstitute.com/wp-content/uploads/2021/01/9-Maths-NCERT-Book-Lines-and-Angles.pdf' -o "$NCERT_PDF"
BODHAN_BACKEND="${DEMO_BACKEND:-local-first}" python -m bodhan_lesson \
  --input "$NCERT_PDF" --langs hi,ta,bn --out .local/demo-ncert </dev/null
python -m json.tool .local/demo-ncert/report.json
```

Inspect the source Markdown for the repeating website footer, then listen
to an example section in each successful language. The eight-page hosted run is summarized in the [README results](../../README.md#results),
with published audio for Hindi, Tamil, and Bengali.

## Demo 2 Indian-language source

Choose a different permitted Hindi-source chapter with 5–30 pages and save
it as `inputs/hindi-chapter.pdf` or set `INDIC_CHAPTER` to its location. If
using another Indian source language, update the source and targets so at
least one target is a different Indian language.

```bash
INDIC_CHAPTER="$PWD/inputs/hindi-chapter.pdf"
BODHAN_BACKEND="${DEMO_BACKEND:-local-first}" python -m bodhan_lesson \
  --input "$INDIC_CHAPTER" --source-lang hi --langs hi,ta --out .local/demo-indic </dev/null
python -m json.tool .local/demo-indic/report.json
python -m json.tool .local/demo-indic/work/pivot.en.json
```

The Hindi target reuses same-language source slots. Tamil exercises
Hindi → English → Tamil. Check Tamil's `segments.json` for pivot links and
the English pivot file for the corresponding attempts. The chapter choice
and published output links are pending.

## Hosted benchmark

Use the first ten pages of the assigned English PDF. This preparation
uses the recipe’s PDF renderer outside the timed invocation. Fresh temporary
directories keep the benchmark independent of saved model calls. The
command times the pipeline from ten page images through export.

```bash
mkdir -p .local
NCERT_PDF="$PWD/inputs/Lines-and-Angles-NCERT.pdf"
TEN_PAGES="$(mktemp -d "$PWD/.local/benchmark-input.XXXXXX")"
BENCHMARK_OUT="$(mktemp -d "$PWD/.local/benchmark.XXXXXX")"
python - "$NCERT_PDF" "$TEN_PAGES" <<'PY'
import sys
from pathlib import Path

from backend.ocr.inputs import discover_pages, render_page

input_path, destination = map(Path, sys.argv[1:])
pages = discover_pages(input_path)[:10]
if len(pages) != 10 or any(page.pdf_offset is None for page in pages):
    raise SystemExit("Provide the assigned PDF with at least ten pages.")
for page in pages:
    with render_page(page) as image:
        image.save(destination / f"{page.page_id}.png")
PY
BODHAN_BACKEND=hosted python -m bodhan_lesson \
  --input "$TEN_PAGES" --langs hi,ta,bn --out "$BENCHMARK_OUT" </dev/null
python -m json.tool "$BENCHMARK_OUT/report.json"
```

Check `quality_targets.hosted_runtime` against the strict under-1,200-second
target. Compare `wall_time_seconds` with the sum of individual attempt times
and verify the report records ten pages, three languages, explicit hosted
mode and fresh attempts. If the run is incomplete, report its elapsed time,
failures and the unverified target. The measured benchmark is pending.

## Available run results

| Evidence | Backend and routing | Outcome | Timing and cost |
| --- | --- | --- | --- |
| Two-page synthetic smoke, Hindi and Bengali | Hosted configuration with provider doubles and one HTTP 429 retry. | Both completed. Each exported two sections with zero fixture CER and measured −16.45 LUFS per section. | Recipe wall time was 1.86 s in the clean-snapshot test. Synthetic accounting only. |
| Three-page English scan, Tamil target, resumed from saved checkpoints | Explicit hosted execution. 292 historical Indic attempts and 11 companion LLM attempts. Zero local attempts or OOM fallbacks. | Completed with misses. Three of five sections exported. | Resume wall time 1.63 s with zero new hosted charges. Historical attempts sum to 1,033.47 s. Known estimated subtotal ₹10.49, with 13 attempts missing cost evidence. |

The short Tamil output is a development run. `p001` and `p002` had
quarantined content. Section `s001` was skipped for quarantined OCR and
`s005` was skipped after chunks `s005-c31` and `s005-c32` remained
unverified. The exported track ran for 199.30 s. `s002` measured
−18.05 LUFS, outside tolerance. `s003` and `s004` measured −16.45 and
−16.49 LUFS. Accepted-chunk CER was 2.60% with 97.03% of normalized
reference characters covered in speech QA. Final exported section coverage
was 3 of 5. There were 33 transport retries. The original run’s end-to-end wall
time is unavailable in the resumed report.

The short Tamil run's known subtotal averages ₹3.50 per input page.
₹8.49 of known charges came from target translation, synthesis and
transcription. Shared OCR contributed ₹2.00. Eleven companion calls had
unknown pricing and two translation attempts lacked completion-token
counts. These values use report rates and usage estimates.

The smoke report is reproducible with the [offline test command](../../README.md#offline-tests).
The short Tamil report and audio are local development artifacts at
`.local/english-scanned/report.json` and `.local/english-scanned/ta/lesson.ta.mp3`.
See the [README results](../../README.md#results) for the later eight-page
live run. The separate 10-page benchmark is pending.

## Evidence review

- Compare selected scan pages with `source/lesson.src.md` and the ordered
  block IDs. Record which pages you checked and any structure or footer misses.
- In each `translation-qa.json`, record quantity, unit, table and LaTeX findings
  and any retry outcomes.
- Listen to one table, equation and figure example when the source contains
  them. Record section IDs, accepted CER, and reasons for any skips.
- Compare each exported section's measured loudness with −16 LUFS ±1. Check
  the lesson's chapters with the command below.

```bash
ffprobe -v error -show_chapters -show_format -of json .local/demo-ncert/ta/lesson.ta.mp3
```

For each live run, record pages, target languages, backend, OOM fallbacks,
retry counts, wall time and cost basis. Use `cost.hosted.known_cost_inr`
and `unknown_cost_attempts` together. For rough per-page cost, divide the
known subtotal by input pages. To compare targets, group translation by the
target code in the attempt's route and group Speak and Transcribe by their
language entity ID. Keep shared OCR, LLM and English-pivot attempts in a
common bucket. Retry charges stay with the original service. Keep
`invocation_cost` alongside historical cost when a run resumes.

## Demo video

[Watch the narrated lesson pipeline demo](https://drive.google.com/file/d/1T9fkQ0G9roSDQvWGSaEdvv0DjKOnpm5s/view?usp=sharing)
