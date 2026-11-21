Current review update: [REVIEW_RESULTS.md](REVIEW_RESULTS.md) records the 37-cue
subtitle refinement, fresh suites and direct terminal recording. The earlier
results below remain historical.

# Latest verified evidence — November 21, 2026

Both live supplements now complete: natural 180s Hindi → English SRT and
115.456s Tamil MP3. See [LIVE_COMPLETION.md](LIVE_COMPLETION.md) for all attempts,
costs, verification and the 129-test checks. Earlier states below are historical.

---

# Remaining evidence work — November 21, 2026

## Latest: runs attempted; provider budget exhausted

After the owner directs using existing credit until the API refuses, both requested
CLI tasks run. `natural_hindi_srt01_20261121` accepts six 25s ASR chunks (150 seconds,
INR0.25) and then receives HTTP 429 ExceededBudget. `tamil_timing03_20261121` reuses
the chapter OCR, writes the new summary and accepts INR0.00462 of translation
before another 429. Both complete-task reports are failed. This is not a success
claim for new subtitles or closer to 120s Tamil speech.

Provider-reported spend: INR10.041874062499994 against INR10.0 budget. The combined
development ledger is INR10.0418740625, matching to rounding. No more paid calls
are sent after the actual credit-exhaustion sign. No payment method/top-up.

Evidence remains local under `outputs/final_suite/`:

- `natural_hindi_srt01_20261121/`: original failed run, trace and capture.
- `tamil_timing03_20261121/`: original failed run and new English summary.
- `natural_hindi_partial_recovery_20261121/`: accepted 150s ASR progress recovered
  from cache, explicitly partial with 30s and English SRT missing. Zero new charge.

`outputs/remaining_evidence_20261121/live_run_ledger.json` records every increment.
`budget_exhaustion_redacted.json` omits the provider's user identifier. Raw reports
are retained privately and unchanged. The earlier preparation/credit notes below
describe history; provider refusal now confirms exhaustion rather than inferring it.

The Cookbook assignment requires opening the PR, not merging it. Upstream review,
workflow approval and merging are the maintainers' actions. The assignment also
assigns native-language output review to its assessors. Obtaining our own fluent
reviewer would strengthen confidence; it is not a stated pre-submission deliverable.
Candidate preparation and replying on the original email thread remain separate.

## Licensed continuous Hindi input prepared

Prepared locally at:
`outputs/remaining_evidence_20261121/natural_hindi02/input/excerpt.wav`.
It is exactly 180 seconds, 2,880,000 samples, mono16kHz PCM16. This is a continuous
excerpt of a published Hindi narration, not a synthetic loop or a montage.
No OCR, ASR, translation or TTS was used to prepare it. Language comes from source
metadata; no model detection or native-speaker certification is implied.

Source: [HealthPhone Hindi recording on Wikimedia Commons](https://commons.wikimedia.org/w/index.php?title=File:HealthPhone-hand-washing-with-soap-and-water-hindi-high.webm&oldid=920519657).
Creator: Nand Wadhwani, HealthPhone.
License: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
The source page records copyright-holder permission through Wikimedia VRT.
The excerpt retains the source license; changes are audio extraction, selection
of decoded audio samples10–190seconds and mono16kHz PCM conversion. No endorsement
is implied. Source and attribution metadata stay outside the model-facing folder.

Published source SHA1: `f89c0b4ea06e4905ae7707dbdae04bdd60fddafa`.
The local provenance manifest also records SHA256 hashes and the exact ffmpeg argv.

Reproduce from the public source using the new standalone preparation helper:

```bash
uv run python scripts/prepare_audio_excerpt.py \
  --source-url https://upload.wikimedia.org/wikipedia/commons/a/ad/HealthPhone-hand-washing-with-soap-and-water-hindi-high.webm \
  --source-page 'https://commons.wikimedia.org/w/index.php?title=File:HealthPhone-hand-washing-with-soap-and-water-hindi-high.webm&oldid=920519657' \
  --creator 'Nand Wadhwani, HealthPhone' \
  --license-url https://creativecommons.org/licenses/by-sa/4.0/ \
  --language hi --offset-seconds 10 --duration-seconds 180 \
  --expected-sha1 f89c0b4ea06e4905ae7707dbdae04bdd60fddafa \
  --out outputs/new_hindi_recording
```

The first acquisition attempt using container time seeking decoded179.9565625s;
it was rejected and retained. Resampling first and trimming exact decoded sample
indices produces180s without padding. `--source-file` can reuse the already
downloaded original, still checking its published checksum.

**Not yet completed:** a Bodhan transcription/English-SRT run on this recording.
That remains separate from input acquisition. It costs at least INR0.30 for ASR
at the assignment's INR0.10/minute, before translation/verification. The existing
ledger remains INR9.7872540625 known/reserved against the original INR10 allocation;
its approximate INR0.21 headroom is an estimate, not a confirmed wallet balance.
The supplied keys cannot read management budgets: `/key/info` returns403 with
`llm_api_routes` scope, while `/v1/models` returns200 without credit fields.

## Tamil timing and model access

The retained Tamil result is90.88s. A closer120s result needs another genuinely
generated/verified summary, not playback slowdown, silence padding or an incomplete
prefix relabeled as a full summary. Cached OCR/valid responses should be reused.
Do not reopen old uncertain requests or remove pending markers to save budget.

The assignment permits self-hosted Bodhan. The original
[Indic-Speak weights](https://huggingface.co/bodhan-ai/indic-speak/tree/main) are
gated behind access/license acceptance. There is no local Bodhan model cache or
configured Hugging Face authentication here. Self-hosting is therefore not an
immediate zero-setup alternative; no account was created or terms accepted.

This change prepares the no-cost input and corrects the handoff classification.
It does not move the existing public PR's evaluation SHA or publish another demo.
Development work follows the private repository PR workflow.

## Existing Tamil roundtrip reviewed without new calls

The retained Bodhan English back-translation was exported in enforced cache-only
mode to `outputs/final_suite/chapter_roundtrip_review_20261121`. All eight topic
groups remain recognizable when compared with the original English summary.
Possible ambiguity: compost is rendered as fertilizer storage/management in the
back-translation. Sentence joins also break the dates/counts wording and leave a
management fragment. These may be back-translation artifacts; they do not prove
the Tamil target is wrong. Detailed findings and original-report hash are retained.
No new inference or native-human review is claimed. The90.88s duration remains.
