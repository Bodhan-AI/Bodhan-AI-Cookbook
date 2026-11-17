# Decision log

| Decision | Alternatives considered | Reason and trade-off | Evidence and verification |
| --- | --- | --- | --- |
| Match the brief with one Python CLI. | Embed an evaluation framework or implement one runner. | Audio, span matching, labels, and baseline reports share an evidence model. A framework would require adapters for most of this contract. | `python -m bodhan_eval --help` and the CLI test in [test_reports.py](tests/test_reports.py). |
| Authenticate the judge via the assignment's three LLM settings. | Call a signed-in Codex subprocess or an OpenAI-compatible endpoint. | Bodhan can substitute its own provider in a clean container. The rubric and judge remain independently testable. | `Providers.judge` in [providers.py](src/bodhan_eval/providers.py). Verified live with Groq `openai/gpt-oss-120b`. |
| Give the judge a fixed rubric and evidence coordinates. | Free-form feedback, one scalar score, or typed findings. | Typed findings support evaluation by category and span. Bad offsets remain visible as rejected entries. | [Judge rubric and parser](src/bodhan_eval/judge.py) and `test_judge_validation_uses_real_offsets`. |
| Use original text spans and measured audio milliseconds. | Normalize and return offsets from the normalized text, or keep an offset map. | Reviewers can locate Indic excerpts exactly. Audio intervals come from samples. The brief leaves legacy label span axes unspecified. | `test_unicode_offsets_and_number_normalization` and `test_pcm_ranges_and_speech_chunk_limits`. |
| Keep waveform evidence and ASR mismatch distinct. | Flag every large character error rate or require evidence of a specific defect. | The recognizer can be wrong. Sustained alignment differences and waveform problems have different evidential value. | [Audio checks](src/bodhan_eval/audio_checks.py). False alarms are measured on the unmutated controls (see the sample report). |
| Use human-labelled examples to fit thresholds. | Treat automatic seeds as calibration truth or fit to a reviewed subset. | Generated controls can contain independent defects. Source-group holdout prevents a mutation from leaking a control into the other split. | `test_calibration_and_localization_are_independent`. |
| Keep unknown span axes out of localization denominators. | Infer whether a bare label points to source or candidate, or use its category alone. | Guessing an axis can create false overlap. We keep category evidence and report localization coverage. | `pairs` and `measure` in [calibration.py](src/bodhan_eval/calibration.py). Confirm the external convention with Bodhan. |
| Cache at the paid request boundary. | Cache final reports or keep exact model responses. | Response reuse permits rescoring, label changes, and comparison without repeating identical calls. Fresh reliability trials bypass the cache. | [Provider cache](src/bodhan_eval/providers.py) and the opt-in live replay test. |
| Account for Bodhan spend with integer micro-rupees. | Derive spend after dispatch or reserve before each request. | Budget checks include concurrent calls and conservative reservations after ambiguous billing. The judge is billed by its configured provider and reported in tokens when price is unavailable. | `Providers._request` and structured run logs. Confirm expected cap scope with Bodhan. |
| Report incomplete work explicitly. | Collapse skipped checks into a clean verdict or expose coverage. | A missing modality, exhausted budget, or unavailable judge is visible. The diff marks changed coverage separately. | `test_cli_ten_real_input_cases_without_credentials`. |
| Use one checked-in dataset of original English sources. | Commit generated media or publish generated results separately. | The reviewer can regenerate the content with their own keys. Keep audio outside Git and link the verified bundle with the demo. | [Demo sources](data/demo.jsonl) and [injector](src/bodhan_eval/demo.py). |
| Follow both package contracts. | Change Cookbook validation or document its generic key alongside the assignment-specific config. | The package reads the brief's per-model keys and the Cookbook can validate the recipe layout. Dependency pins live in `requirements.txt` and supply `pyproject.toml`. | [.env.example](.env.example), [package metadata](pyproject.toml), and Cookbook validation. |

## Cookbook validation

`python scripts/validate_recipe.py examples/translation_speech_quality` passes on a clean checkout. In a working directory it reports a local `.env`, even though Git ignores that file. The all-recipes Cookbook test also fails on the untouched upstream base `ae51852`, because the validator does not recognize the AndroidSpeak layout.

## Follow-up after the offline audit

| Decision | Reason | Verification |
| --- | --- | --- |
| Treat malformed media paths as item errors. | A bad filename should preserve that row's text checks and allow the rest of the batch to finish. | `test_cli_keeps_rows_after_invalid_audio_path` exercises a missing file and a path containing a null character. |
| Compare baseline findings by overlapping evidence in a shared field. | Equal category counts can hide both a resolution and a regression. Text alignment preserves a finding when its passage moves. | `test_baseline_matches_evidence_across_runs` checks both outcomes. |
| Verify uncertain omissions on short clips with another segmentation. | One transcript can omit spoken content. A recognisable second transcript must corroborate the same missing span. A recovered span clears the finding. | `test_short_speech_omission_requires_corroboration` covers repeated, recovered, and empty alternate transcripts. Live ASR behavior still needs measurement. |
| Accept usable judge verdicts at the cache boundary. | Malformed responses can be billable and should not prevent a later valid request. Judge cache version 2 leaves prior unchecked entries behind. | `test_unusable_judge_verdict_does_not_poison_cache` verifies rejection, successful replacement, and warm replay through SQLite. |
| Validate baselines and source splits before provider work. | Invalid reports or contradictory source groups can be detected without spending credit. | The CLI performs both checks before creating the provider client. |
| Report calibration support and author attribution. | An explicit calibration file signals which sources can tune thresholds. A reading of the results requires real positive and negative counts. | `calibration.json` exposes both support counts and whether tuning was possible. `items.jsonl` records supplied author attribution. |

## Localization, baseline identity, and quantity association

| Decision | Reason | Verification |
| --- | --- | --- |
| Pair localized evidence before assigning category-only detections. | The original matcher could move an overlapping flag to a different label while keeping the same detection count. Loose detections now have a separate count. | `test_category_only_match_cannot_displace_localized_evidence` first reported zero localized matches. It now retains one match out of two. |
| Preserve expected-quantity anchors and compare shared check coverage. | A changed wrong value can leave the same source fact wrong. Missing judge coverage is independent of a completed numeric check. | `test_baseline_uses_expected_quantity_and_completed_checks` and `test_baseline_resolves_only_findings_with_shared_check_coverage`. |
| Match uniquely shared literal labels before comparing unpaired numbers globally. | A quantity belongs to an entity or table cell. Absolute position would flag legitimate reordering. Literal matching leaves translated or ambiguous labels for the semantic checks. | `test_quantity_associations_survive_reordering` covers a unit phrase, labelled fields, and a Markdown table. |

## Hosted integration

| Decision | Reason | Verification |
| --- | --- | --- |
| Use Indic-Translate through `/v1/chat/completions`. | The legacy `/translate` route returned HTTP 404 in the first hosted run. The live gateway accepts explicit language codes and `max_output_tokens`, which preserves the request budget in either direction. | A one-token live probe returned `finish_reason: length` and reported one output token. `test_translation_uses_hosted_chat_contract` verifies request shape, metering, and truncation handling. |
| Use Groq `openai/gpt-oss-120b` for the first hosted pilot. | The test account has access to this OpenAI-compatible text model. A consistent judge makes verdicts and cache behavior comparable across the run. | A direct chat request succeeded. The judge receives text evidence and returns verdicts. Bodhan still handles generation, back-translation, and speech recognition. |

## Detection-quality pass after the first measured demo

The first scored demo (34 rows, held-out recall 66.7%) showed where detection was lost. Each change below targets one of those observed failures.

| Decision | Evidence that motivated it | Verification |
| --- | --- | --- |
| The judge points at numbered words, not character offsets. | Verdicts for an injected open→closed flip and an injected added sentence were discarded as "no verifiable evidence". The judge's character offsets in Indic text did not land on the words it described. | `test_judge_reply_tolerates_wrappers_and_keeps_indic_free_reasons`. On the same 34 rows, held-out recall rose from 66.7% to 100%. |
| Strip Indic letters from the judge's rationale instead of rejecting the finding. | A rationale such as "बंद means closed" was valid, but rejecting it lost the finding. The ground rule is that the model never writes stored Indic text. That still holds, because evidence is cut from the original. | Same test asserts the stored rationale is English-only. |
| Parse the verdict object out of reasoning or prose, sample unusable replies again, and use JSON mode at temperature 0 with a plain fallback. | Across the 200-row FLEURS run, 25 judge calls returned empty text and 12 returned an invalid verdict. The panel will use a different LLM. | `test_unusable_judge_verdict_does_not_poison_cache`, `test_judge_falls_back_when_json_mode_is_rejected`. |
| Five attempts with jitter, a shared per-key cooldown after HTTP 429, and a separate judge concurrency limit. | 105 of about 200 judge calls in the FLEURS run failed on HTTP 429 after three quick attempts. | Provider tests. On the rerun of the 34 labelled rows, one judge call out of 34 still failed. |
| Isolate unexpected exceptions per row. | One unexpected exception inside `gather` would end the batch without a report. | The CLI writes an `internal` check error for that row and keeps going. |
| Accept label spelling variants, and keep the row when its labels are unusable. | A label such as `"severity": "Major"`, or float offsets, rejected the whole row. That would cost hidden-set rows their evaluation. | Label errors are reported under `invalid_calibration_rows`. The row is still scored. |
| Move a threshold only with three or more positive and three or more negative reviewed items. | With one positive and one negative, the fit could select 0.58 and switch on weak evidence for a whole category. | `test_calibration_and_localization_are_independent`. |
| One explanation per text passage. | A changed digit was also reported as a judge mistranslation of the same word, counting as a false category. | `accepted()` keeps the strongest overlapping text candidate. |
| Detect repetition from leftover alignment material. | A sentence spoken twice produced 96–169 inserted characters and no repetition flag. | `tests/test_speech_fixes.py`. The three stored repetition clips are now flagged. |
| Judge silence relative to the clip's own level, and let a fully recognised transcript demote a silence flag. | Two quiet but intelligible FLEURS recordings were flagged as 100% silent, while ASR read them at 1% CER. | FLEURS silence false alarms went from 5 to 0. Seeded 3 s gaps are still flagged at 0.99. |
| Add a broadband-noise detector based on zero-crossing rate. | "Noise" is a garbled-audio case the brief names, and nothing measured it. Clean Bodhan TTS never exceeds 0.2 s of noise-like frames. Inserted noise gives runs of about 2 s. | `test_signal_check_flags_inserted_noise_but_not_clean_tone`. |
| Normalize Indian grouping and scale words, treat `$5` as currency, count table cells, and check inline markup and link syntax. | A reviewer probe found false alarms on `2,50,000`, `25 लाख` and `$5 and $10`, and missed dropped cells and broken bold. | `tests/test_text_fixes.py` (77 cases). |
| Make the injector more varied, without an LLM. | The first injector used one mutation per category, which overstates generalization. | `tests/test_injector.py`. The demo set now has 15 mutation methods. |
| Measure judge consistency by default and cache the repeat. | The brief's consistency target was unmeasured unless the panel passed a flag. A fresh uncached repeat would break byte-identical cache replays. | `--judge-repeat` defaults to 10. The repeat is stored under its own cache key. |
| Report the flag rate on unmutated controls. | False alarms were "Unmeasured" without reviewed clean rows. Controls give a large, conservative denominator. | `quality.unmutated_controls` in `summary.json`, and a row in `summary.md`. |

## Improvement round on the 231-row demo

The first full run, with Cerebras `qwen-3.8-27b` as judge, gave 94.7% recall on injected errors. Every miss was inspected.

| Decision | Evidence that motivated it | Verification |
| --- | --- | --- |
| Treat a contiguous missing block of 20% or more of the script as strong omission evidence (0.86), and re-check every omission not already corroborated by speaking rate. | Mid-clip cuts lost 34–39% of the script in one stretch but stayed at 0.68. Clean demo clips never lost more than 4.8% in one stretch, and FLEURS recordings never more than 6.9%. | Offline replay of stored transcripts: 13 of 13 injected speech omissions flagged, and no speech flags on 108 clean demo clips or 199 FLEURS clips. |
| Detect phrases the transcript says more often than the script (4+ words), comparing script phrases by character similarity. | A looped table row of times and temperatures was spoken as English number words, so it could not align to `08:30` and `21 °C`. | Offline replay: 11 of 11 repetitions flagged. A script that legitimately repeats a phrase is not flagged even when ASR spells a word differently. |
| Accept native-digit list numbers (`১.`, `२.`) as Markdown list markers. | Bodhan localizes list numbers in Bengali, and CommonMark then saw a lost list, which was a false format flag on a clean control. | `test_native_digit_list_numbers_are_still_a_list`. |
| Never rename a capitalized word that only starts a sentence, line, list item, heading or table cell. | The entity injector turned "Read" into "Curie" and a table header into "Lima". Those were meaning and format edits, not entity edits. | Injector tests. The demo was regenerated, and 6 entity variants changed. |
