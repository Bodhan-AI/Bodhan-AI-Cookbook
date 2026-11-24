# French unsupported-language negative test — verified

Run date: 22 November 2026 (IST); exact start/end clock time not captured.

Command used:

```powershell
.\.venv\Scripts\python.exe -m bodhan_agent `
  --task "Translate the attached paragraph into French and save the French translation as a Markdown file." `
  --files .\sample_data\paragraph.txt `
  --out .\outputs\demo_unsupported_french_01 `
  --max-steps 20 `
  --max-cost-inr 1
```

**Observed:** `not_completed`; 1 step; 1.14 seconds; no tool events; zero Bodhan costs estimated; no output artifacts. Explanation correctly says French is unsupported by Indic-Translate.

**Limitations:** This tests *text-to-French*, not the assignment's *video-to-French* example. The automatic error description incorrectly mentions OCR; Windows PowerShell showed UTF-8 punctuation incorrectly under its default text decoding. Neither changes the factual outcome.

**Evidence to retain locally:** `outputs/demo_unsupported_french_01/report.json` and `result.md` (sanitize private local paths before publicly sharing).
