# Public evidence and final submission checklist

The README and PR draft have honest results but **cannot yet provide live evidence URLs or a recorded video**. Finish these before the final email. Do not commit `.env`, caches, generated media or input files containing private data.

1. From your original development project, collect for each of the six runs: `result.md`, `report.json`, `trace.jsonl`, and the meaningful generated artifacts (e.g., Bengali WAV, Hindi SRT, Marathi Markdown, Tamil MP3 + summary, Telugu report/translations, French refusal result). Keep original trace/report files unchanged.
2. If hosting in Google Drive, create a view-only folder and set **Anyone with the link → Viewer**, or use another host accessible without authentication. Check in a private browser. These files are rights-cleared test material; do not publish secrets, local personal records, credentials or unauthorized media.
3. Before sharing, search JSONL files for credentials and sensitive data. If Windows absolute paths are redacted for privacy, clearly label the shareable copy as redacted and retain originals.
4. Add actual per-run evidence links to README and PR body. Do not invent links.
5. Record a ≤5 minute video showing the app, real files and trace, external MCP client, successful Bengali/Hindi/Telugu/Marathi cases, the **partial** Tamil summary, and corrected French refusal. Show the Tamil 5/8 OCR limitation rather than calling it an all-pages success.
6. In the final submission clone, sync patched source and tests, run `python -m pytest -q examples/bodhan_task_agent/tests`, `python scripts/ci_validate.py --base-ref main`, `python -m ruff check scripts/ tests/`, `git diff --cached --check`, and `git status`. Note the two unrelated repo-wide failures on native Windows if relevant.
7. Push final changes on `assignment/tool-agent-KowshiqKatta` and open a PR into `Bodhan-AI/Bodhan-AI-Cookbook:main`, using the PR template. Obtain the exact SHA with `git rev-parse HEAD` **after the final push**.
8. Reply on the original Bodhan email thread with the PR URL and SHA before the stated deadline. The assignment says later commits are ignored for evaluation; do not send SHA until ready.
