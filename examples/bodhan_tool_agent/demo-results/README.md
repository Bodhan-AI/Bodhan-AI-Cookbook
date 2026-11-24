# Demo run evidence

These are real run outputs, not simulated evaluation results. Each retained task
folder includes `result.md`, `report.json`, `trace.jsonl`, and the text
artifacts produced by that run. Local absolute paths were redacted from the
published result/report summaries. Source media and generated audio are not
included; some report artifact lists therefore refer to media files omitted
from this repository.

No live input media or audio is stored in this folder.

| Task | Status | Recorded result | Evidence |
|---|---|---|---|
| 1 | `completed` in report; review needed | OCR and translation coverage passed; speech back-transcription passed. The English summary is truncated, so this is not claimed as a clean task pass. | [result](./task-01/result.md) · [report](./task-01/report.json) · [trace](./task-01/trace.jsonl) |
| 2 | `completed` | Plain transcript saved. The available demo input was MP3, not WAV. | [result](./task-02/result.md) · [report](./task-02/report.json) · [trace](./task-02/trace.jsonl) |
| 3 | `completed` | Worksheet OCR and Marathi translation artifacts saved; report coverage checks passed. | [result](./task-03/result.md) · [report](./task-03/report.json) · [trace](./task-03/trace.jsonl) |
| 4 | `completed` | All five clips transcribed; one probable Telugu transcript translated into English. Language identification is inconclusive, semantic accuracy was not verified, and the generated summary is truncated. | [result](./task-04/result.md) · [report](./task-04/report.json) · [trace](./task-04/trace.jsonl) |
| 5 | `completed` | Bengali translation saved; speech back-transcription coverage was 0.948. | [result](./task-05/result.md) · [report](./task-05/report.json) · [trace](./task-05/trace.jsonl) |
| 6 | `impossible` (expected) | No Bodhan tool operations or audio artifacts. Video dubbing is unsupported, and French is absent from the supported Indic-Speak language list. The recorded planner explanation is truncated. | [result](./task-06/result.md) · [report](./task-06/report.json) · [trace](./task-06/trace.jsonl) |

`estimated_cost_inr` is the recorded Bodhan estimate, not actual billing; LLM
charges are unavailable in these reports.
