# Submission preparation

This is preparation against the **Video Dubbing Pipeline** assignment, not the earlier Tool Server assignment. Pending items must be replaced with verified evidence before submitting. A completed demo render does not establish a clean-clone hosted run or native-speaker certification.

See `quality_engineering_audit.md` for all seven quality targets and all five engineering expectations. The current report distinguishes offline runtime, unverified acoustic sync and reported CER warnings.

## Required submission gates

| Assignment item | Evidence / next action |
| --- | --- |
| Self-contained recipe under `examples/` | `examples/video_dubbing/`; recipe validator passes. |
| One-command setup from a clean clone | `sh scripts/setup.sh`; local setup works. Docker build/CLI and all 507 tests pass on Linux ARM64; a literal fresh final-commit clone remains pending. |
| Required CLI and output layout | README documents `python -m bodhan_dub --input ... --langs hi,ta,bn --out ...`; real-media mocked smoke and saved-run execution pass. The live hosted stages completed through automatic recovery continuations. A fresh full hosted invocation is unverified. |
| Bodhan-only speech/translation | Indic-Transcribe, Indic-Translate, Indic-Speak. Local Silero only detects activity; Groq only processes English text. |
| Architecture, workflow and design | `docs/design.md` has two Mermaid diagrams, meaning checks, retries, failure handling and trade-offs. |
| Demo output in Hindi, Tamil, Bengali | All three current MP4s, SRT/VTT files and segment reports exist locally, 12 groups each and zero skips. [Public outputs folder](https://drive.google.com/drive/folders/1w0TS-eCZH6u00KkhyyKf5epPGO1QRk8y?usp=share_link). |
| Tests and Cookbook checks | 507 recipe tests passed; recipe validator and dependency consistency pass. Root lint/rule checks previously passed. `make check` was attempted and fails on unrelated pre-existing AndroidSpeak recipe files. Retain the diagnostic and disclose it. |
| Keys/tokens and large media absent from submission | Environment files and generated media stay outside submitted recipe contents. **Review the exact files to stage before committing**; this document does not certify a Git index it has not inspected. |
| Recording, at most 5 minutes | Outline below. [Public recording (4:02)](https://drive.google.com/file/d/1S2th1qKBc0D8y3mh1jVgW1Pn-JsAigd0/view?usp=sharing). |
| README and PR link outputs/recording | The README and PR draft include the verified Drive links. |
| Open PR with required title/template | Draft below; **no PR opened**. |
| Reply to original assignment email | Include PR URL and final commit SHA before deadline. **No email sent**; later pushes are not evaluated. |

## Recording outline — target 4 minutes 40 seconds

Record only after the clean setup is verified. Hide keys, private environment files and unrelated windows. The assignment requires a full run on the supplied demo and its outputs; an offline rerender must not be presented as fresh hosted execution.

| Time | Show / explain |
| --- | --- |
| 0:00–0:25 | Recipe, purpose, Bodhan model boundary and required three languages. |
| 0:25–0:55 | Successful clean setup/build, private environment-variable names only, and the exact required CLI. |
| 0:55–2:10 | Show the actual hosted stage logs, explain the fitting continuation, and replay the verified final render. Identify the cached stages and the offline replay. |
| 2:10–3:20 | Open Hindi, Tamil and Bengali MP4s. Play short representative passages, show subtitles and unchanged video duration. |
| 3:20–4:10 | Open `report.json`: coverage, bounded speeds, CER warnings, loudness, actual run cost and runtime. Show one recovery/caching example and explain estimated source timing. |
| 4:10–4:40 | Architecture/workflow diagrams, test evidence, limitations and where evaluators find outputs. |

Suggested narration: “The LLM plans and reviews English. Bodhan performs every transcription, translation and speech-synthesis operation. Python enforces costs, retries and complete-phrase placement. This demo retains video duration and silent gaps; precise word synchronization and native naturalness are not certified.”

The final saved-audio replay took 18.4 seconds. The hosted validation and its continuations cost an estimated ₹20.79. A fresh hosted benchmark is deferred until after submission.

## Public deliverables to upload after approval

- Three current `video.<lang>.mp4` files from `outputs/recording-validation-verified/final/`.
- Matching subtitles and `segments.<lang>.json`, plus English transcript and `report.json`.
- The recording of at most five minutes.
- Optional sanitized quality/cost evidence, without API keys, authorization headers, private account data or `.env` files.

Check each final link without signing in. Keep large videos outside Git. Link the same final artifacts from both README and PR. Uploading/publishing has not been authorized by preparing this document.

## PR title

`[Agentic Eng Assignment] Video dubbing — Siddharth`

## PR description draft

Copy the [PR description](pr_description_draft.md). It follows the assignment template and includes both diagrams.

## Final email draft — do not send until the PR commit is final

Subject: Video Dubbing Take-Home Submission — Siddharth

Hi Bodhan Team,

Please find my Video Dubbing take-home submission below.

PR: [final PR URL]
Final commit SHA: [exact evaluated commit]
Demo recording: [public recording URL]

Setup, output links, design notes, test evidence and known limitations are included in the recipe README and PR description.

Thank you,
Siddharth
