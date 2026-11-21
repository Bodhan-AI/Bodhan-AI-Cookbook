# Task result: 🟡 partial

**Task:** Summarise this page in three bullet points in English

## Answer

The run stopped before the planner finished: Reached the 600s time limit during a planner call. 3 artifact(s) passed their checks and are listed below; they may not fully answer the task.

## Deliverables

- [artifacts/textbook_page_ocr.md](artifacts/textbook_page_ocr.md) — ocr (kn); produced by `ocr_document` via indic-ocr; verification: not verified

  ```text
  ಜೇನುನೊಣಗಳು ದೊಡ್ಡ ಗುಂಪುಗಳಲ್ಲಿ ಒಟ್ಟಿಗೆ ವಾಸಿಸುತ್ತವೆ, ಅವುಗಳನ್ನು
  ವಸಾಹತುಗಳು ಎಂದು ಕರೆಯಲಾಗುತ್ತದೆ. ಪ್ರತಿ ವಸಾಹತುವಿನಲ್ಲಿ ಒಂದು ರಾಣಿ
  ಜೇನುನೊಣ, ಕೆಲವು ನೂರು ಗಂಡು ಜೇನುನೊಣಗಳು ಮತ್ತು ಸಾವಿರಾರು
  ಕೆಲಸಗಾರ ಜೇನುನೊಣಗಳು ಇರುತ್ತವೆ. ಕೆಲಸಗಾರ ಜೇನುನೊಣಗಳು ಹೂವುಗಳಿಂದ
  ಮಕರಂದ ಮತ್ತು ಪರಾಗವನ್ನು ಸಂಗ್ರಹಿಸುತ್ತವೆ. ಹೂವಿನಿಂದ ಹೂವಿಗೆ
  ಚಲಿಸುವಾಗ ಅವು ಪರಾಗವನ್ನು ಸಾಗಿಸುತ್ತವೆ, ಇದು ಸಸ್ಯಗಳು ಬೀಜಗಳು ಮತ್ತು
  ಹಣ್ಣುಗಳನ್ನು ಉತ್ಪಾದಿಸಲು ಸಹಾಯ ಮಾಡುತ್ತದೆ. ಇದನ್ನು ಪರಾಗಸ್ಪರ್ಶ …
  ```

- [artifacts/textbook_page_ocr_en.md](artifacts/textbook_page_ocr_en.md) — translation (en); produced by `translate_text` via indic-translate; verification: PASS, back-translation chrF 0.911

  ```text
  Bees live together in large groups called colonies. Each colony has a queen bee, several hundred male bees, and thousands of worker bees. The worker bees collect nectar and pollen from flowers. As they move from flower to flower, they carry pollen, which helps plants produce seeds and fruits. This is called pollination. About one-third of the food we eat depends on the pollination by bees and othe …
  ```

- [artifacts/summary_en.md](artifacts/summary_en.md) — text (en); produced by `write_text` via planner (English); verification: not verified

  ```text
  - Bees live in colonies with a queen, male drones, and thousands of worker bees.
  - Workers pollinate plants by carrying pollen while foraging, supporting roughly one‑third of global food supplies.
  - Farmers use beehives to boost yields, but excessive pesticide use harms bees, prompting a shift to natural protection methods.
  ```


## Assumptions

- The image contains Kannada text suitable for OCR.
- The translated text will be accurate enough for a brief summary.

## Limitations

- Stopped early (time_limit); outputs were not confirmed complete by the planner.

## Cost, time and steps

- Status: `partial` (stop reason: `time_limit`)
- Bodhan cost: ₹0.0000 billed; budget ₹20.0
- Planner LLM: openai/gpt-oss-20b — 6 calls, 38249 prompt + 1545 completion tokens (not priced (no rate supplied); tokens reported only)
- Time: 600.01 s (multi-step, target 300 s: missed); missed by 300 s (600 s against 300 s); tool calls 0% of the time (0 s), mostly step 2 (ocr_document, 0 s); planner replies 1% of the time (7 s) (6 calls); 99% of the time (593 s) outside tool calls and planner replies (planner retries and their waits, preflight, bookkeeping)
- Steps: 6 of 30; cache hits: 3

| Step | Tool | OK | Latency (s) | Cost (₹) | Verification |
|---|---|---|---|---|---|
| 2 | `ocr_document` | yes | 0.047 | 0 |  |
| 3 | `translate_text` | yes | 0.015 | 0 | pass |
| 4 | `read_text` | yes | 0.0 | 0 |  |
| 5 | `write_text` | yes | 0.015 | 0 |  |

Full step-by-step trace: [trace.jsonl](trace.jsonl). Machine-readable summary: [report.json](report.json).
