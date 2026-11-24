# Submitting this to the Bodhan AI Cookbook

This repository is the working copy. The assignment is submitted as a **pull request**
against [Bodhan-AI/Bodhan-AI-Cookbook](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook),
not as a standalone repository.

## Steps

1. Fork the Cookbook and read its `CONTRIBUTING.md`.
2. Create a branch named `assignment/quality-harness-<your-github-handle>`.
3. Copy this repository's contents into `examples/bodhan-quality-harness/` of the fork
   (the folder must be self-contained; `.env` must not be copied).
4. Run `make check` in the Cookbook root.
5. Open a PR titled `[Agentic Eng Assignment] Quality harness — Hema Krishna`, using
   `PR_DESCRIPTION.md` as the body.
6. Reply on the assignment email thread with the PR link **and the final commit SHA**.
   They evaluate that exact commit; later pushes are ignored.

## Before you open the PR

- [ ] `data/demo.jsonl` regenerated with `python tools/make_demo.py` so every
      Indian-language string comes from Indic-Translate. The committed file is a
      development fixture — see the README section "Demo data".
- [ ] `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` set and a run completed with the judge
      enabled, so the reported recall reflects the judge and not just the free checks.
- [ ] `pytest -q` passes from a clean clone.
- [ ] No `.env`, no keys, no generated audio in the repository.
- [ ] Sample report in `docs/sample_report/` regenerated from the Bodhan-produced demo data.

## Endpoints to confirm on the first live run

The client assumes OpenAI-compatible paths. If Bodhan's docs differ, override the base URL
or the path rather than editing the client:

- `POST {BODHAN_BASE_URL}/chat/completions` — Indic-Translate
- `POST {BODHAN_BASE_URL}/audio/speech` — Indic-Speak
- `POST {BODHAN_BASE_URL}/audio/transcriptions` — Indic-Transcribe

The harness has never been run against the live API in the environment where it was
written (the network blocked `api.bodhan.ai`), so this is the first thing to check.
