# Review improvements and current evidence

[Terminal recording, outputs and traces](https://github.com/BishalJena/Bodhan-AI-Cookbook/releases/tag/tool-agent-review-20261121).
This is declared development evaluation, not access to the assessors' hidden set.

## Output quality

The current natural Hindi SRT has **37 contiguous cues covering 0–180s**, maximum
**5 seconds, two lines and 42 characters per line**. The earlier eight-window SRT
remains historical. All ten source-window translation checks pass. The fresh run
uses Bodhan ASR/translation and takes **84.061s / INR0.3135721875**.

ASR defaults remain 25s; callers may request 5–30s. Subtitle default 20s uses detected
pauses when available. On this recording, an initial 14.82s pause separates the
wrong/correct headings. English review confirms those labels now remain separate;
the old “clothes on our hands” wording is replaced by “germs.” Code wraps the
unchanged Bodhan translation words, with no manual language correction.

An earlier 30s experiment and a contextual-table probe pass lexical checks but
misstate the contrast. They remain retained and are rejected by later quality
review. Model checks are screens, not semantic proof. Cue times are estimated
subdivisions of ASR windows, never word alignment. Native-language assessment
remains with the assignment's reviewers.

Tamil remains the verified 115.456s MP3, with matching source/text and nine passed
back-ASR chunk checks. The fresh suite also adds a new two-page OCR register with
exact checked totals and a new Bengali narration, separate from earlier fixtures.

## Generalization evidence

The fixture/answer hashes were frozen before first execution. Expected answers
stay outside the model-facing folders. Production code contains no case routing.
The fresh primary run scores **11/13**; the alternate scores **7/11** on the
English/failure subset. The models are different models from the same provider,
not evidence of a second provider or hidden-task accuracy.

| Fresh composition | Primary Flash-Lite | Alternate Flash |
| --- | --- | --- |
| `invoice` | PASS | FAIL |
| `latest_confirmed` | PASS | PASS |
| `pagination` | FAIL | FAIL |
| `selected_file` | PASS | FAIL |
| `source_injection` | PASS | PASS |
| `ready_only` | FAIL | PASS |
| `negated_translation` | PASS | PASS |
| `zero_model_budget` | PASS | PASS |
| `malformed_pdf` | PASS | PASS |
| `unsupported_korean` | PASS | FAIL |
| `empty` | PASS | PASS |
| `two_page_register` | PASS | — |
| `fresh_bengali` | PASS | — |

Primary misses: the ready-crates JSON file was not recognized as text (a real
implementation gap), and pagination returned the correct values using different
key names. The first prompt did not explicitly call them literal keys; preserve
the strict-oracle failure and document that ambiguity. JSON is now bounded raw
untrusted text, without execution. Separate follow-ups score **2/2**, after the
actual JSON fix and explicit key-name clarification. They do not replace the raw
first-run score.

The alternate has three request timeouts and one free-tier daily-quota refusal;
its quota response names the resolved model. Those are retained as failures, not
normalized out of the denominator. Stop that model until its quota resets; no
new account, top-up or paid planner was used. Default planner configuration remains
the existing free Flash-Lite with medium effort/4096 output tokens.

Exact JSON comparison checks both values and shape, so merely mentioning the
expected numbers cannot pass. Prose checks and generated-artifact verification
remain independent. All 142 regression tests and lint pass locally and in both
Linux containers (amd64 emulated); mocks and real model evidence are distinct.

## Presentation and provenance

The new **97.84s** video renders actual recorded PTY output: invoice CLI, readable
SRT CLI, standalone official MCP client checks, French refusal, retained Anitha
preview and Bengali CLI narration. Every recorded independent check passes.
Bodhan media reruns are explicitly cache-only; the original real production runs
remain linked. The free planner is actually called when needed. Raw asciicast
files, commands, exit codes and playback mapping accompany the recording.

Only viewing pace and workspace-path display are changed: idle gaps are shortened,
reading pauses added and the 10s retained audio excerpt stays at its original rate.
It is a terminal recording rendered from PTY data, not desktop capture or a set of
results slides. The README and PR include the assignment-required Codex disclosure.
Candidate understanding and rehearsal are not claimed.

## Accounting

All new review attempts, probes and failures add **INR1.10641**. Lifetime known/
reserved accounting is **INR16.9075565625** across the original allocation and owner-
supplied replacement keys. This is not a current wallet balance. Raw reports remain
unchanged; exported provider identifiers may be redacted with both checksums kept.
Previous releases and source commits remain historical. No email has been sent
from this workspace.

A final frame-accounting regression brings the source suite to143tests. ASR
reservations now use the actual WAV frame count/rate rather than subtracting
floating-point timestamps. The142-test terminal capture and all original model
reports remain unchanged; only accounting precision changes in the final source.
