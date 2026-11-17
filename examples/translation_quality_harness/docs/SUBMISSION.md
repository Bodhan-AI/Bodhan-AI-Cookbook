# Submission notes

- Recipe branch: `assignment/quality-harness-Purv-Kabaria`.
- All recipe code, tests, diagrams and reports live in this folder.
- Generated audio and the 126-second terminal demo are public release assets, never Git objects in the submitted branch history.
- The final demo runs with `--offline --judge off --max-cost-inr 0`; no more model credit is used.
- Cached determinism, baseline diff and test evidence: [OFFLINE_VERIFICATION.json](OFFLINE_VERIFICATION.json).
- The LLM judge is implemented and tested against mocked OpenAI-compatible responses; actual full-coverage consistency remains unmeasured after earlier judge credit exhaustion. The evaluator can configure its own provider.
- All demo/calibration error labels identify the programmatic injector. No human adjudication is claimed.
- Cookbook-wide validator portability/native-Android fixes are separate in [PR #25](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/pull/25), following the one-recipe-per-PR rule. The upstream global test gate needs that fix independently of this recipe. The recipe test suite and recipe validator pass on their own.
- Docker configuration is supplied; this machine has no running Docker engine. Fresh Python-environment setup and an offline replay are verified instead.

The assignment also requires replying on the original email thread with the recipe PR URL and exact final commit SHA. The reply must identify the commit evaluated; later pushes are ignored. A ready-to-send reply is prepared outside the public repository after the final SHA exists.
