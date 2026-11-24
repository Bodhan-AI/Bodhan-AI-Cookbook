# Draft PR description — incomplete

Complete the real dataset, integration tests, measurements, and video before using this draft.

## Summary
The harness evaluates translations and speech, combines evidence from deterministic checks and an
LLM judge, and produces per-item reports and baseline comparisons.

## How to run
See the recipe README for setup and the required command. Confirm these instructions from a clean clone.

## Architecture
See `docs/architecture.md`; verify the diagram against the final code.

## Harness design
See `docs/flow.md` and `docs/design_notes.md`; verify check order and speech selection.

## Where the LLM is used, and why
The judge reads the source and translation and returns categorized evidence using the rubric in
`src/bodhan_eval/rubric.md`. Record measured consistency and the tested provider here.

## Bodhan models used
Record verified model names, endpoints, hosting and settings after live testing.

## Results
Add measured recall, false alarms, localization, and category accuracy per language and category on
the actual demo, with sample counts. Distinguish injector from human labels.

## Demo video
[Watch the demonstration](https://drive.google.com/file/d/1dj-dtYGGQCWNwLau13VujNblcoXU0mbt/view?usp=sharing).

## Trade-offs and known limitations
Record observed misses, checker failures, calibration support and sampling trade-offs here.

## Cost and runtime
Measure per 100 items with and without speech, and explain excluded judge/provider costs.

## AI assistance
Accurately describe the coding assistants used and the candidate's own review and understanding.
