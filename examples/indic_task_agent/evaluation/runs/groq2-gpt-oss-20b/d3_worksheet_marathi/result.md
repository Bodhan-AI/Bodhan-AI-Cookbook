# Task result: ✅ completed

**Task:** Translate this worksheet into Marathi and keep the table

## Answer

The worksheet has been OCRed from the image, translated into Marathi while preserving the table structure, and saved as a markdown file.

## Deliverables

- [artifacts/worksheet_marathi.md](artifacts/worksheet_marathi.md) — translation (mr); produced by `translate_text` via indic-translate; verification: PASS, back-translation chrF 0.937

  ```text
  विज्ञान कार्यपत्रिका ७ - आपली सौरमाला
  
  नाव: ________________ वर्ग: ६ दिनांक: ________________
  
  अ. तक्ता वाचा आणि खालील प्रश्नांची उत्तरे द्या.
  
  | ग्रह | सूर्यापासून अंतर | उपग्रहांची संख्या | प्रकार |
  | --- | --- | --- | --- |
  | बुध | १ | ० | खडकाळ |
  | पृथ्वी | ३ | १ | खडकाळ |
  | मंगळ | ४ | २ | खडकाळ |
  | गुरू | ५ | ९५ | वायू राक्षस |
  | शनी | ६ | १४६ | वायू राक्षस |
  
  १. या तक्त्यात कोणत्या ग्रहाला स …
  ```


## Assumptions

- The worksheet text is originally in English.
- The OCR step captured the entire content accurately.
- Translation into Marathi in native script preserves tables and numeric data.

## Intermediate artifacts

- [artifacts/worksheet_ocr.md](artifacts/worksheet_ocr.md) — ocr, indic-ocr

## Cost, time and steps

- Status: `completed` (stop reason: `finished`)
- Bodhan cost: ₹0.0000 billed; budget ₹20.0
- Planner LLM: openai/gpt-oss-20b — 4 calls, 22585 prompt + 937 completion tokens (not priced (no rate supplied); tokens reported only)
- Time: 101.41 s (multi-step, target 300 s: met)
- Steps: 4 of 30; cache hits: 17

| Step | Tool | OK | Latency (s) | Cost (₹) | Verification |
|---|---|---|---|---|---|
| 2 | `ocr_document` | yes | 0.031 | 0 |  |
| 3 | `translate_text` | yes | 0.016 | 0 | pass |

Full step-by-step trace: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).
