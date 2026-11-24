# Design notes

The pipeline is deterministic code with an LLM at a few decision points. Code owns timing, measurement and
every retry loop; the LLM only makes judgement calls about English text, and each of its answers is checked
by code before it is used. Every number below was measured on real runs or synthetic tests, not tuned to the
demo video.

## Where the LLM decides

| Decision point | What the LLM decides | How its answer is checked | If it fails or is unavailable |
|---|---|---|---|
| Punctuation (`punctuate.py`) | Case and punctuation of the raw ASR text (ALL CAPS, no punctuation). | The word sequence must equal the input, ignoring case and punctuation. | Retry, then a rule-based fallback. |
| Boundary review (`review.py`) | Which consecutive sentences are fragments of one sentence. | Ids consecutive and in order; letters and digits unchanged; token count equals the sum of the sources. | The merge is rejected; sentences stay as they are. |
| Shorten English (`condense.py`) | A shorter English version of a unit whose dub is too long for its slot. | Rule check (shorter, no new numbers, few new words), then a separate YES/NO meaning judge. Up to 3 candidates; the 3rd aims at a milder target. | An LLM error is never counted as "NO"; the clip keeps its fitted version and any overrun is reported. |
| Catch-up (`dub_language.recondense`) | The same shortening, for the real time left when a clip is pushed late by its neighbour. | Same checks, then re-translate, TTS and QA. | The earlier clip is kept if QA fails or there is no gain. |

The shortened English always goes back through `indic-translate`: the LLM never writes Indian-language text.
Results are cached on disk by prompt and input, so a re-run gives the same text.

## How sync is achieved and checked

- **Timing comes from audio, not from the ASR.** The hosted ASR returns no timestamps. Speech regions from
  Silero VAD (on the separated voice when there is music) give every start and end. Words are spread over the
  regions, and sentence breaks snap to real pauses.
- **Pause-level alignment.** Each chunk is cut at its pauses into pieces that are transcribed on their own. The
  pieces' letters are aligned to the full transcript, which fixes where a sentence break falls. A break is moved
  only when the match is unambiguous. On a synthetic set of 606 breaks, exact starts went from 74.3% to 98.8%.
- **Fit before placing.** Each clip is fitted to its source window: first by shrinking or widening its own pauses,
  then by speed (0.85–1.2×, pitch-preserving), then by shorter English.
- **Placement.** A clip's first sound goes on the source start. A clip is late only when the previous one is still
  sounding, and catch-up refits such clips.
- **Closed loop.** The placed track is measured with the same detector and edge rule as the source. Heard starts
  and ends are corrected and the track is measured again; a round that makes things worse is undone.
- **Independent verification.** `sync_check.py` measures the final speech track, not our own bookkeeping:
  per-unit start/end error, speech-activity IoU and phrase onsets (both do not depend on how units were cut),
  plus a cross-correlation of the MP4 against the planned mix to catch mux or timebase shifts.

## How quality is checked

- **Translation:** script share, looping repetition, length ratio, finish reason; a batch that returns the wrong
  number of lines is re-translated line by line.
- **Speech:** every clip is transcribed back by `indic-transcribe` and compared with its text (character error
  rate; digits and Latin words are wildcards because TTS reads them as words). A repeat, an omission or a high
  CER triggers a fresh TTS sample (up to 2).
- **ASR junk:** some replies decay into one character repeated (`ୄୄୄ…`), on music and on clean speech. They are
  random: 3 such clips re-sent 17 times came back clean every time. A junk reply is retried and never cached; if
  it stays junk, a chunk is re-split like a cut-off reply and a QA result counts as unknown, not as a bad clip.
- **Cut-off ASR replies:** ALL-CAPS replies stop at about 206–218 characters, the same way on every retry. Such a
  chunk is split at a pause and the halves are transcribed again. On 3 captioned clips, WER went 12.7→8.1%,
  22.0→7.6% and 14.0→9.4%.
- **Glossary (`--glossary`):** Indic-Translate has no glossary field and drops placeholder markers, so a
  rendering cannot be forced. Every translation is checked for each glossary term its English contains (words
  may carry case endings: a word matches when it starts with the rendering's stem). A unit that misses one is
  re-translated once with the rendering already written into the English; the retry is kept only if it keeps
  more terms and adds no other failure. Shortening must keep every glossary term (rule check and prompt). The
  report lists every term still missing.
- **Code-mixed (`--code-mixed`):** the prompt asks for `<Language> (code-mixed)`. Its output is mostly Latin
  (24–44% native letters on 9 live outputs), so the script check asks for some native script (an untranslated
  line has none) and nothing but native and Latin letters. QA joins a Latin word with its hyphen-attached ending
  (`model-ஐ`) into one wildcard, the way it is spoken.

## How failures are recovered

- Bodhan calls retry 429 and 5xx with backoff. `429 budget_exceeded` is not retried; that key then fails fast.
- A failed segment never stops its language; a failed language never stops the others. The run still writes
  partial outputs and the report, and exits non-zero.
- The LLM client paces itself from rate-limit headers and stops at a daily cap; every LLM step above has a
  fallback, so the run finishes without the LLM, with more overruns.
- Every step can be traced afterwards: `run.log.jsonl` holds each log record with its stage, language and
  segment, and one final record per segment with its English, translation, TTS takes, fit, shortenings, QA
  result and placement. A crash is logged with its traceback under the language it happened in.
- Inputs whose video codec cannot go into an MP4 (ProRes from MOV, VP8 from WebM: both made the mux fail before
  this was handled) are re-encoded to H.264 instead of failing the language.

## Measured decisions

| Decision | Evidence |
|---|---|
| ASR hears the original mix; timing uses the separated voice | WER 7.7% (original) vs 7.6% (separated voice) on 3 captioned clips: a tie, so the safer original is used. |
| Separation only below a 30 dB voice/background gap | Clips with music measured 4.9–19.8 dB; clean clips 33–46 dB. |
| Chunks cut at pauses ≥ 0.6 s, not 0.35 s | The finer cut doubled the cuts and changed 3.75% of words, mostly for the worse. |
| One edge rule (30 dB below the speech level) for source and dub | The old floor-relative rule heard dub starts 0.05 s early on average because the dub sits in digital silence. |
| Female voice as the default in every language | Genders from the Cookbook rules file; measured pitch agrees for all 12 pairs. |
| Glossary match on the word stem, not the exact word | On the demo's Tamil track, an exact match finds 12 of 26 uses of கோணம் ("angle"); the stem rule finds all 26. |
| Glossary retry is a best effort, never a rejection | Where Indic-Translate's word differed from the wanted one, writing the rendering into the English kept it in 2 of 5 live cases (hi/ta/bn). |
| Code-mixed QA joins hyphenated Latin words | A real Tamil code-mixed clip went from a false "omission" (CER 0.154) to CER 0; the 147 demo clips score the same as before. |
