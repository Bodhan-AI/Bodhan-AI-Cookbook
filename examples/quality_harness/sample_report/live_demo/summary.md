# Held-out demo results

The live Bodhan run completed 116 of 120 Hindi, Tamil and Bengali translations, audio clips, back-translations and transcriptions. Four translation requests remained rate-limited. The synthetic evaluation used these outputs as parents and kept related items in the same split.

| Language | Held-out errors | Deterministic detections | With paired Bodhan ASR |
|---|---:|---:|---:|
| Hindi | 180 | 107 | 159 |
| Tamil | 181 | 106 | 153 |
| Bengali | 176 | 106 | 159 |
| All | 537 | 319 (59.4%) | 471 (87.7%) |

The paired check compares a clip's Bodhan transcript with a separately supplied baseline transcript. It correctly categorized 86 of 92 speech omissions and 70 of 92 repetitions. The default CLI does not assume a baseline clip exists. The paired check does not provide timestamps, and localization overlap fell from 96.6% to **65.4%**, below the 70% target. Category accuracy was **79.9%** among eligible spatially matched detections. Human calibration and clean-item false alarms were unmeasured.

The paced ASR run covered 184 mutant clips in 918.2 seconds, reused 38 cached clips and reserved ₹1.30 for new attempts. This run isolated speech recognition. A cold 200-item run and comparable per-100-item timings with and without speech were not measured. Groq GPT-OSS 120B completed matching repeated judgments on one pilot source in each language. All 12 judge checks in the later injected-case diagnostic failed due to rate limits, HTTP 400 or rubric validation. Judge performance on injected errors remains unmeasured.

[Per-language and per-category metrics](summary.json)

To reproduce the paired score after generating the source reports, run this from the recipe directory:

```bash
python -m bodhan_eval.speech_compare --parents outputs/demo-bodhan-final --asr-report outputs/demo-synthetic-expanded/asr-held-out-paced --offline-report outputs/demo-synthetic-expanded/offline-report --out outputs/paired-comparison.json
```

The command uses local reports and makes no API calls. The source reports and clips must first be generated through the [demo workflow](../../docs/phase3_workflow.md). The [text-only demo outputs](../../data/demo_translations.jsonl) support a credential-free translation smoke run. Generated audio is kept outside Git. A public recording and audio link are pending.
