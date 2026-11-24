# Task result: 🟡 partial

**Task:** Which of these clips are in Telugu? Translate those into English

## Answer

The Telugu clips are **clip_1.mp3** and **clip_3.ogg**.

English translations:
- **clip_1.mp3:** “The library is open until eight p.m. during exam week.”
- **clip_3.ogg:** “The bus to the school picnic will leave at seven thirty sharp.”

Other detected languages: clip_2.wav is Tamil, clip_4.m4a is Hindi, and clip_5.flac is Kannada.

Both translation files were produced and passed translation verification. The run is marked partial only because the automated final delivery-coverage check rejected the results.

## Deliverables

- [artifacts/clip_1_english.md](artifacts/clip_1_english.md) — translation (en); produced by `translate_text` via indic-translate; verification: PASS, back-translation chrF 0.741

  ```text
  the library is open until eight p. m. during exam week
  ```

- [artifacts/clip_3_english.md](artifacts/clip_3_english.md) — translation (en); produced by `translate_text` via indic-translate; verification: PASS, back-translation chrF 0.745

  ```text
  the bus to school picnic will leave at seven thirty sharp
  ```


## Assumptions

- Language identification is based on automatic speech transcription.

## Limitations

- Automated final delivery-coverage validation rejected the completion despite both requested Telugu translations being produced and passing translation verification.

## Intermediate artifacts

- [artifacts/clip_1.txt](artifacts/clip_1.txt) — transcript, indic-transcribe
- [artifacts/clip_1.json](artifacts/clip_1.json) — json, indic-transcribe
- [artifacts/clip_4.txt](artifacts/clip_4.txt) — transcript, indic-transcribe
- [artifacts/clip_4.json](artifacts/clip_4.json) — json, indic-transcribe
- [artifacts/clip_5.txt](artifacts/clip_5.txt) — transcript, indic-transcribe
- [artifacts/clip_5.json](artifacts/clip_5.json) — json, indic-transcribe
- [artifacts/clip_2.txt](artifacts/clip_2.txt) — transcript, indic-transcribe
- [artifacts/clip_2.json](artifacts/clip_2.json) — json, indic-transcribe
- [artifacts/clip_3.txt](artifacts/clip_3.txt) — transcript, indic-transcribe
- [artifacts/clip_3.json](artifacts/clip_3.json) — json, indic-transcribe

## Cost, time and steps

- Status: `partial` (stop reason: `finished`)
- Bodhan cost: ₹0.0000 billed; budget ₹20.0
- Planner LLM: gpt-6.1-sol — 8 calls, 46140 prompt + 1935 completion tokens (not priced (no rate supplied); tokens reported only)
- Time: 63.16 s (multi-step, target 300 s: met)
- Steps: 11 of 30; cache hits: 9

| Step | Tool | OK | Latency (s) | Cost (₹) | Verification |
|---|---|---|---|---|---|
| 2 | `transcribe_audio` | yes | 0.187 | 0 |  |
| 5 | `transcribe_audio` | yes | 0.203 | 0 |  |
| 6 | `transcribe_audio` | yes | 0.203 | 0 |  |
| 3 | `transcribe_audio` | yes | 0.234 | 0 |  |
| 4 | `transcribe_audio` | yes | 0.25 | 0 |  |
| 7 | `translate_text` | yes | 0.016 | 0 | pass |
| 8 | `translate_text` | yes | 0.031 | 0 | pass |

Full step-by-step trace: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).
