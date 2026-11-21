Current review update: [REVIEW_RESULTS.md](REVIEW_RESULTS.md) records the 37-cue
subtitle refinement, fresh suites and direct terminal recording. The earlier
results below remain historical.

# Completed live supplements — November 21, 2026

[Current recording, outputs and traces](https://github.com/BishalJena/Bodhan-AI-Cookbook/releases/tag/tool-agent-live-20261121).
Earlier releases and every failed attempt remain historical evidence.

| Case | Actual outcome | Runtime and accounting |
| --- | --- | --- |
| Natural Hindi | English SRT, eight contiguous windows covering 0–180s; eight translation checks pass | 91.444s and INR0.32626 including prior 19.522s/INR0.25; incremental cost INR0.07626 |
| Tamil chapter | 115.456s MP3, matching Tamil text and English source; all translation and nine speech-chunk back-ASR checks pass | Final run 240.763s, INR1.1771269792, 30 steps; earlier attempts remain counted |

The Hindi input is a continuous 180s excerpt of a HealthPhone recording with
multiple natural voices, replacing the earlier synthetic loop. Creator: Nand
Wadhwani, HealthPhone; [source](https://commons.wikimedia.org/w/index.php?title=File:HealthPhone-hand-washing-with-soap-and-water-hindi-high.webm&oldid=920519657),
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Sample-exact extraction
selects decoded seconds 10–190; attribution/provenance accompany the evidence.
English subtitles are a Bodhan-generated adaptation with coarse 25s windows.
Awkward phrasing and source-boundary cuts remain possible, not fluent certification.

Tamil uses all eight pages of the original fictional school-garden scan. The final
MP3 passes the explicit 115–125s interval. Its PCM is an exact join of the nine
original generated chunks, without added padding or playback-rate transformation.
The fixed voice is Anitha; audio is AI-generated. A cache-only English back-translation
supports review of all eight topics, with fragmentary joins and wording differences
still disclosed. It is not a native Tamil assessment.

## Preserved attempts and planning improvement

The first replacement-key run renders 101.717333, 128.768, 108.629333 and 110.677333s,
then stops partial at 600.006s with INR4.4370455208 retained. A larger-limit carry is
rejected before dispatch, leaving its guard intact. A later free-planner timeout
costs 0; another default-effort calibration hits 45 steps, costing INR0.06884.
Pending tombstones and prior artifacts remain. This is not first-attempt success.

Optional provider-neutral reasoning effort and output caps address the observed
planning difficulty. Nine new regressions cover forwarding, pre-dispatch reservation,
invalid configuration and continuation identity. Default behavior remains omitted
effort/1500 tokens. The final correction uses the existing free Flash-Lite model with
medium effort/4096 tokens. It progresses through Tamil drafts of 1322, 1428 and 1523
characters before speech. Character counts guide planning; measured audio decides.
No demo-task routing is added.

All 129 tests and lint pass locally and in Linux arm64/amd64 containers (amd64 under
local emulation). Transport-mocked regressions remain separate from real runs.
The final Tamil run is under five minutes; cumulative attempts exceed that latency
target. No first-attempt or unseen-task success guarantee is implied.

## Accounting and handoff

Replacement-key development adds INR5.7592725. Lifetime known/reserved accounting is
INR15.8011465625, including failed attempts and uncertain reservations. This is not
a current wallet balance. The earlier INR10 service refusal remains historical;
the owner subsequently supplied replacement keys. No payment/account/billing-plan
action or paid planner was performed by this workspace.

Original local evidence folders under `outputs/final_suite/`:

- `natural_hindi_srt02_newkeys_20261121/`
- `tamil_timing04_newkeys_20261121/` through `tamil_timing08_reasoning_20261121/`
- `tamil_timing08_roundtrip_review_20261121/` (zero-cost review export)

The release includes a manifest, redacted exports, acceptance checks and the ledger.
Raw local reports remain unchanged. Candidate review/rehearsal and the original
email-thread reply remain separate. Native-language review belongs to the assessors.
No human review, email send, hidden score or maintainer merge is claimed.
