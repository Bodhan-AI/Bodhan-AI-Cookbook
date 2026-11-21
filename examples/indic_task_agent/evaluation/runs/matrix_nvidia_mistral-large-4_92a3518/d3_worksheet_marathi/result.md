# Task result: ✅ completed

**Task:** Translate this worksheet into Marathi and keep the table

## Answer

The worksheet photo was OCRed (detected as English, 1 table found) and translated into Marathi with the table preserved as a Markdown table. The Marathi file contains the full worksheet: title ("Science Worksheet 7 — Our Solar System"), the Name/Class/Date line, section A with the planet table (Planet | Position from the Sun | Number of moons | Type, rows for Mercury, Earth, Mars, Jupiter, Saturn), the three questions, and section B. Verification passed: Devanagari script share 1.0, all 15 numbers preserved, table shape preserved (6 rows x 4 columns), back-translation chrF 0.937, no warnings.

## Deliverables

- [artifacts/worksheet_ocr.md](artifacts/worksheet_ocr.md) — ocr (en); produced by `ocr_document` via indic-ocr; verification: not verified

  ```text
  Science Worksheet 7 — Our Solar System
  
  Name: ________________ Class: 6 Date: ________________
  
  A. Read the table and answer the questions below.
  
  | Planet | Position from the Sun | Number of moons | Type |
  | :--- | :---: | :---: | :--- |
  | Mercury | 1 | 0 | Rocky |
  | Earth | 3 | 1 | Rocky |
  | Mars | 4 | 2 | Rocky |
  | Jupiter | 5 | 95 | Gas giant |
  | Saturn | 6 | 146 | Gas giant |
  
  1. Which planet …
  ```

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

- The worksheet was in English (OCR detected English/Latin script); it was translated to Marathi in native Devanagari script
- The deliverable is the translated Markdown file itself (with the table kept as a table), not an image or audio
- The intermediate English OCR artifact is also delivered for reference

## Limitations

- The translation is a text/Markdown file; the original photo's layout, box for drawing, and fill-in blanks are represented as text lines/underscores as in the OCR
- OCR of a photo can occasionally misread handwriting-style fonts; the detected content was a clean printed worksheet and verification passed with no warnings

## Cost, time and steps

- Status: `completed` (stop reason: `finished`)
- Bodhan cost: ₹0.0000 billed; budget ₹5.0
- Planner LLM: mistralai/mistral-large-4-1t-a52b — 6 calls, 35598 prompt + 1442 completion tokens (not priced (no rate supplied); tokens reported only)
- Time: 228.5 s (multi-step, target 300 s: met)
- Steps: 5 of 30; cache hits: 17

| Step | Tool | OK | Latency (s) | Cost (₹) | Verification |
|---|---|---|---|---|---|
| 2 | `ocr_document` | yes | 0.047 | 0 |  |
| 3 | `translate_text` | yes | 0.032 | 0 | pass |
| 4 | `read_text` | yes | 0.0 | 0 |  |

Full step-by-step trace: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).
