# Make a two-minute Tamil audio summary of this chapter.

**Status:** failed

Produce a two-minute Tamil audio summary of the 8-page chapter.pdf. This requires reading the PDF (OCR), drafting an English summary, translating it to Tamil, and speaking ~120 s of Tamil audio.

## Why this is not complete

- `BUDGET_EXCEEDED`: BUDGET_EXCEEDED: the cheapest honest plan costs ~Rs 2.60 against a Rs 0.05 cap. Arithmetic: (1) Reading the chapter needs OCR — 8 pages x Rs 0.20/page = Rs 1.60. (2) Two minutes of speech = 120 s x ~13.5 chars/s = ~1620 characters minimum (Tamil runs 15-25% longer, so realistically ~1900-2000 chars); speak_text at Rs 6/10k chars = Rs 0.97+ for the audio alone — already ~19x the entire budget before any reading cost. (3) Translating the ~1620-char summary to Tamil adds ~Rs 0.02. Total ~Rs 2.60. Even the absolute floor — skipping OCR entirely and speaking only the required two minutes — is Rs 0.97 > Rs 0.05, and a summary cannot be made without reading the chapter. No honest plan fits the budget; a shorter clip or larger budget would be needed. — No model was called and no output was produced.

## What was done

Nothing was run; no model was called.


## Assumptions

- 'Two-minute' is the required speech length: 120 s, i.e. >=1620 characters of final Tamil text. (Rule 7 fixes the char budget for audio length; this drives the speak_text cost floor.)
- The PDF's language/script is unknown, but this does not affect the refusal — OCR is required to read it in any language, and the speech cost alone already exceeds the budget. (Refusal is decided on cost arithmetic, not on input language.)
- No cheaper substitute (e.g. a shorter clip, text-only summary) is offered, per rule 5. (Producing a substitute and calling it done is prohibited when the requested plan is unaffordable.)

## Verification

No artifact reached verification.

## Artifacts

None.

## Cost and time

Rs 0.00 of the Rs 0.05 budget in 18s.

## Limitations

- None beyond the model's own quality.
