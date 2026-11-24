# Task result: ✅ completed

**Task:** I only care about things that are happening on a specific day of the week. Find those in this folder and give me Punjabi versions of just those.

## Answer

Found two matching items and created Punjabi versions in Gurmukhi:

- health_camp_punjabi.md — Health camp on Saturday, 9 am–4 pm, at the Panchayat office. Includes both pages of the original notice.
- market_note_punjabi.md — This week’s market moves to Thursday instead of Friday because of the festival.

Excluded water_announcement.mp3: it says “tomorrow,” but does not name a weekday.

Both Punjabi translations passed verification.

## Deliverables

- [artifacts/market_note_punjabi.md](artifacts/market_note_punjabi.md) — translation (pa); produced by `translate_text` via indic-translate; verification: PASS, back-translation chrF 0.854

  ```text
  ਇਸ ਹਫ਼ਤੇ ਤਿਉਹਾਰ ਕਾਰਨ ਸਪਤਾਹਿਕ ਬਾਜ਼ਾਰ ਸ਼ੁੱਕਰਵਾਰ ਦੀ ਬਜਾਏ ਵੀਰਵਾਰ ਨੂੰ ਹੋਵੇਗਾ।
  ```

- [artifacts/health_camp_punjabi.md](artifacts/health_camp_punjabi.md) — translation (pa); produced by `translate_text` via indic-translate; verification: PASS, back-translation chrF 0.976

  ```text
  <!-- page 1 -->
  ਸਿਹਤ ਕੈਂਪ ਸੂਚਨਾ
  
  ਸ਼ਨੀਵਾਰ ਨੂੰ ਸਵੇਰੇ 9 ਵਜੇ ਤੋਂ ਸ਼ਾਮ 4 ਵਜੇ ਤੱਕ ਪੰਚਾਇਤ ਦਫ਼ਤਰ ਵਿੱਚ ਮੁਫ਼ਤ ਸਿਹਤ ਕੈਂਪ ਲਗਾਇਆ ਜਾਵੇਗਾ। ਡਾਕਟਰ ਬਲੱਡ ਪ੍ਰੈਸ਼ਰ, ਸ਼ੂਗਰ ਲੈਵਲ ਅਤੇ ਅੱਖਾਂ ਦੀ ਜਾਂਚ ਕਰਨਗੇ। ਕਿਰਪਾ ਕਰਕੇ ਆਪਣਾ ਆਧਾਰ ਕਾਰਡ ਨਾਲ ਲੈ ਕੇ ਆਓ।
  
  <!-- page 2 -->
  ਟੀਕਾਕਰਨ ਅਨੁਸੂਚੀ
  
  ਪੰਜ ਸਾਲ ਤੋਂ ਘੱਟ ਉਮਰ ਦੇ ਬੱਚਿਆਂ ਨੂੰ ਸਵੇਰੇ ਦਸ ਵਜੇ ਤੋਂ ਦੁਪਹਿਰ ਬਾਰਾਂ ਵਜੇ ਤੱਕ ਪੋਲੀਓ ਦੀਆਂ ਬੂੰਦਾਂ ਦਿੱਤੀਆਂ ਜਾਣਗੀਆਂ। ਗਰਭਵਤੀ ਔਰਤਾਂ ਕਮਰਾ ਦੋ ਵਿੱਚ ਜਾਂਚ ਲਈ ਨਰਸ ਨ …
  ```


## Assumptions

- Since no particular weekday was specified, selected items mentioning any explicitly named weekday.
- Punjabi versions are text in Gurmukhi, with the full matching documents retained.

## Intermediate artifacts

- [artifacts/market_english.md](artifacts/market_english.md) — translation, indic-translate
- [artifacts/water_source.txt](artifacts/water_source.txt) — transcript, indic-transcribe
- [artifacts/water_source.json](artifacts/water_source.json) — json, indic-transcribe
- [artifacts/health_camp_source.md](artifacts/health_camp_source.md) — ocr, indic-ocr
- [artifacts/health_camp_english.md](artifacts/health_camp_english.md) — translation, indic-translate
- [artifacts/water_english.md](artifacts/water_english.md) — translation, indic-translate

## Cost, time and steps

- Status: `completed` (stop reason: `finished`)
- Bodhan cost: ₹0.0076 billed; budget ₹20.0
- Planner LLM: gpt-6.1-sol — 7 calls, 45598 prompt + 1432 completion tokens (not priced (no rate supplied); tokens reported only)
- Time: 58.39 s (multi-step, target 300 s: met)
- Steps: 12 of 30; cache hits: 15

| Step | Tool | OK | Latency (s) | Cost (₹) | Verification |
|---|---|---|---|---|---|
| 4 | `translate_text` | yes | 0.0 | 0 | pass |
| 3 | `transcribe_audio` | yes | 0.141 | 0 |  |
| 2 | `ocr_document` | yes | 3.938 | 0 |  |
| 7 | `read_text` | yes | 0.0 | 0 |  |
| 5 | `translate_text` | yes | 0.031 | 0 | pass |
| 6 | `translate_text` | yes | 0.016 | 0 | pass |
| 8 | `read_text` | yes | 0.0 | 0 |  |
| 9 | `read_text` | yes | 0.0 | 0 |  |
| 11 | `translate_text` | yes | 1.657 | 0.00148 | pass |
| 10 | `translate_text` | yes | 3.25 | 0.00608 | pass |

Full step-by-step trace: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).
