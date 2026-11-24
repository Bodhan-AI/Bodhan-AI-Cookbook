# Task result: ✅ completed

**Task:** Translate this worksheet into Marathi and keep the table

## Answer

The worksheet has been OCR-extracted and fully translated into Marathi (Devanagari script). The table is preserved in Markdown format.

**What was done:**
1. OCR — Extracted the English worksheet text, including a 4-column x 6-row Markdown table of planets.
2. Translation — Translated every element (title, name/class/date fields, instructions, table headers and rows, numbered questions, and the drawing prompt) into Marathi.
3. Verification — Passed all checks: table shape 6x4, all 15 numbers preserved, back-translation chrF score 0.937 (excellent).

**Translated content summary:**
- Title translated to: "Science Worksheet 7 - Our Solar System" in Marathi
- Table columns: Planet | Distance from Sun | Number of Moons | Type (all in Marathi)
- All five planet rows (Mercury, Earth, Mars, Jupiter, Saturn) with numbers in Devanagari numerals
- Three answer questions and the drawing/writing prompt fully translated

The artifact worksheet_marathi.md contains the complete Marathi worksheet ready to use.

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

- Source language was English (confirmed by OCR with 0.9 confidence)
- Numbers were converted to Devanagari numeral forms as is standard for Marathi text
- Table format was kept as Markdown, as that preserves structure for digital use

## Intermediate artifacts

- [artifacts/worksheet_ocr.md](artifacts/worksheet_ocr.md) — ocr, indic-ocr

## Cost, time and steps

- Status: `completed` (stop reason: `finished`)
- Bodhan cost: ₹0.0000 billed; budget ₹5.0
- Planner LLM: claude-sonnet-4-6 — 7 calls, 53101 prompt + 1945 completion tokens (not priced (no rate supplied); tokens reported only)
- Time: 43.7 s (multi-step, target 300 s: met)
- Steps: 6 of 30; cache hits: 17

| Step | Tool | OK | Latency (s) | Cost (₹) | Verification |
|---|---|---|---|---|---|
| 2 | `ocr_document` | yes | 0.047 | 0 |  |
| 3 | `translate_text` | yes | 0.032 | 0 | pass |
| 4 | `read_text` | yes | 0.0 | 0 |  |

Full step-by-step trace: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).
