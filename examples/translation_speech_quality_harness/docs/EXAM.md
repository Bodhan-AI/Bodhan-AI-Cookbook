# Independent blind exam

The harness was graded once on an exam it had never seen, to estimate how it does on Bodhan's hidden dataset (~200 items, 4–5 languages including one never demoed, about half clean). This page says how the exam was built, how it was kept blind, exactly what was run, and every number that came out. Nothing was tuned after the run: the harness code at the exam run (commit `3981173`) is the code submitted (later commits change only documentation and report data).

## How the exam was built

- **By a separate project.** A different Claude Code project in its own folder built the exam from its own brief. It never saw this repository; this repository never saw its sources, seeding notes or answers.
- **Only Bodhan models**, the same rules as the harness: Indic-Translate for translations, Indic-Speak for audio, errors planted programmatically or by perturbing the English and re-translating. No LLM wrote Indian-language text.
- **Contents.**

| File | What it holds |
|---|---|
| `exam.jsonl` | 146 scoring items in **6 languages**: hi, ta, bn (demoed), te and mr (held out in development), **gu (never seen anywhere)**; 98 with 24 kHz WAV audio; about half clean; no labels |
| `calibration.jsonl` | 20 separate items with labels inline (passed as `--calibration`, as a hidden test might) |
| `labels.jsonl` | gold labels for the 146 scoring items (`{type, severity, span, span_on}`, spans on the translation or, for omissions, on the source); clean items `[]` |
| `run_b/exam.jsonl` | a second version of the same 146 items, for the `--baseline` comparison |

- **Checked before delivery** by the exam project: one JSON object per line in the SPEC format, unique `(id, lang)`, every audio path resolves to a 24 kHz WAV, no labels in the two exam files, every label span inside its text, calibration items disjoint from the scoring items.

## How it was kept blind

- This repository's rules (`CLAUDE.md`) forbid opening, reading or searching the exam folder. The harness session received only four file paths and passed them to the command line; the folder's answer material (`SEEDING_NOTES.md`, `seeding/`, `clean/qa/`, `exam/`) was never opened.
- The judge model and every default were fixed before the run (judge bake-off on the demo data; speech and entity fixes D46–D48 validated on the demo and on real Bodhan outputs).
- Run once. The numbers below are reported as they came out.

## What was run

```bash
# Run A: the blind run, with the 20-item calibration file
python -m bodhan_eval --dataset "<exam>/exam.jsonl" --langs hi,ta,bn,te,mr,gu --out outputs/exam \
  --calibration "<exam>/calibration.jsonl" --max-cost-inr 14 \
  --judge-models moonshotai/kimi-k3.1-pro --llm-extra-body '{"reasoning": {"effort": "low"}}'

# Run B: the second version of the same items, compared with run A
python -m bodhan_eval --dataset "<exam>/run_b/exam.jsonl" --langs hi,ta,bn,te,mr,gu --out outputs/exam_b \
  --baseline outputs/exam --calibration "<exam>/calibration.jsonl" --max-cost-inr 16 \
  --judge-models moonshotai/kimi-k3.1-pro --llm-extra-body '{"reasoning": {"effort": "low"}}'

# Scoring, after the run: the gold labels are read only here
python -m bodhan_eval.score --report outputs/exam --gold "<exam>/exam.jsonl" --labels "<exam>/labels.jsonl"
```

## Results (run A)

All 146 items loaded and scored (0 bad lines, 0 skipped, 0 check failures; the 20 calibration items ran alongside and are kept out of the scores).

| Measure | Value |
|---|---|
| Labelled errors caught (recall) | **91.6 %** (76 / 83 labels) |
| Items with an error that got a flag | **96 %** (72 / 75) |
| Clean items flagged (false alarms) | **4.2 %** (3 / 71) |
| Precision (flags matching a label) | 81.5 % |
| Localisation (flag overlaps the labelled span) | 93.4 % |
| Category correct (same category group / exact) | 98.7 % / 94.7 % |
| Missed labels the review queue holds | 2 of 7 |

**By severity:** critical 10 / 10, major 48 / 51 (94 %), minor 18 / 22 (82 %).

**By language** (recall / false alarms on clean items, MQM-style score of the run):

| Language | Items | Recall | False alarms | MQM |
|---|---:|---:|---:|---:|
| hi | 25 | 92.3 % | 0 % | 88.5 |
| ta | 24 | 85.7 % | 9.1 % | 86.0 |
| bn | 24 | 92.9 % | 0 % | 84.5 |
| te (held out in development) | 24 | 100 % | 9.1 % | 87.1 |
| mr (held out in development) | 24 | 85.7 % | 8.3 % | 86.6 |
| **gu (never seen)** | 25 | **92.9 %** | **0 %** | 89.1 |

**By category** (labels; recall; precision of the flags of that category; localisation):

| Category | Labels | Recall | Precision | Localisation |
|---|---:|---:|---:|---:|
| addition | 6 | 100 % | 100 % | 100 % |
| omission | 8 | 100 % | 100 % | 100 % |
| mistranslation | 8 | 100 % | 85 % | 100 % |
| number | 12 | 100 % | 57 % | 100 % |
| term | 8 | 100 % | 100 % | 100 % |
| wrong_language | 6 | 100 % | 100 % | 100 % |
| format | 15 | 93 % | 76 % | 79 % |
| speech_omission | 7 | 86 % | 100 % | 100 % |
| speech_garbled | 7 | 71 % | 100 % | 60 % |
| speech_repetition | 6 | 50 % | 75 % | 100 % |

**Judge reliability on the exam:** the same categories under re-ordered prompts on 97 % of items (verdict 97.6 %; per language 93–100 %), the same answer to an identical re-ask on 96 % of the repeat sample, a tie-break replica on 3 % of items. The judge answered in strict `json_schema` throughout.

**Calibration:** the 20-item file had fewer than 5 labels per category, so no threshold was tuned (the defaults stayed, as designed); on those 20 items the run caught 92 % of labels with 0 false alarms.

**Checks that ran:** back-translation, numbers, script, length and the judge on all 146 items; transcription and waveform checks on the 98 with audio; format on the 48 with Markdown, tables or LaTeX; names on the 73 with names or terms; no reference translations were given, so chrF++ did not apply.

## Run B against run A (`diff.md`)

Same 146 items, second version; 0 added, 0 removed. `diff.md` lists **18 new problems and 16 fixed ones**, severity-sorted with evidence (for example a number changed 7.25 → 2.75 in Hindi, a unit changed l → ml in Gujarati):

| Language | Flagged before → now | New problems | Fixed problems |
|---|---|---:|---:|
| bn | 13 → 13 | 2 | 2 |
| gu | 12 → 12 | 2 | 3 |
| hi | 11 → 12 | 4 | 3 |
| mr | 11 → 11 | 2 | 2 |
| ta | 14 → 15 | 5 | 2 |
| te | 14 → 13 | 3 | 4 |

With `--fail-on-regression` the same command would have exited with code 3 (new major or critical problems), the CI use case.

## Cost and runtime

| Run | Bodhan | Judge (Kimi K3.1 Pro) | Time |
|---|---:|---:|---:|
| A (146 + 20 calibration items, 98 with audio) | ₹3.83 (transcription ₹3.67, back-translation ₹0.16) | $1.24 | 37.6 min |
| B (same items, second version; unchanged outputs from the cache) | ₹0.98 | $0.13 | 3.6 min |

Run A shared Bodhan's transcription quota with another job (a voice comparison) for most of its run; the time is rate-limit waiting (HTTP 429, waited out, never dropped), not computation.

## What it says about the harness

- **Text errors are caught reliably**, including in a language never seen: every addition, omission, mistranslation, number, term and wrong-language label, and 93 % of format labels; Gujarati scored like the demoed languages.
- **Speech is the weak side.** Repetitions and garbled stretches that the transcriber smooths over leave no trace in the transcript (repetition 3 / 6, garbled 5 / 7). The waveform check only sees silence, clipping and noise, not a cleanly repeated phrase. A waveform self-similarity check is the next step.
- **Number flags are the least precise** (57 %): 23 number flags, 13 matching a label, while recall is 100 %. The likely cause, seen on the demo data, is a second flag for the same changed number (the number check and the judge at different spans, or one per occurrence); the 10 unmatched flags were not inspected item by item, to keep the exam's answer material closed.
- **False alarms stay low** (3 of 71 clean items), all in languages other than Hindi and Bengali.
