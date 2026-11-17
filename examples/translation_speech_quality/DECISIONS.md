# Decision log

| Decision | Alternatives considered | Reason and trade-off | Evidence and verification |
| --- | --- | --- | --- |
| Match the brief with one Python CLI. | Embed an evaluation framework or implement one runner. | Audio, span matching, labels, and baseline reports share an evidence model. A framework would require adapters for most of this contract. | `python -m bodhan_eval --help` and the CLI test in [test_reports.py](tests/test_reports.py). |
| Authenticate the judge via the assignment's three LLM settings. | Call a signed-in Codex subprocess or an OpenAI-compatible endpoint. | Bodhan can substitute its own provider in a clean container. The rubric and judge remain independently testable. | `Providers.judge` in [providers.py](src/bodhan_eval/providers.py). Real provider compatibility awaits credentials. |
| Give the judge a fixed rubric and evidence coordinates. | Free-form feedback, one scalar score, or typed findings. | Typed findings support evaluation by category and span. Bad offsets remain visible as rejected entries. | [Judge rubric and parser](src/bodhan_eval/judge.py) and `test_judge_validation_uses_real_offsets`. |
| Use original text spans and measured audio milliseconds. | Normalize and return offsets from the normalized text, or keep an offset map. | Reviewers can locate Indic excerpts exactly. Audio intervals come from samples. The brief leaves legacy label span axes unspecified. | `test_unicode_offsets_and_number_normalization` and `test_pcm_ranges_and_speech_chunk_limits`. |
| Keep waveform evidence and ASR mismatch distinct. | Flag every large character error rate or require evidence of a specific defect. | The recognizer can be wrong. Sustained alignment differences and waveform problems have different evidential value. | [Audio checks](src/bodhan_eval/audio_checks.py). Quantify false alarms after live review. |
| Use human-labelled examples to fit thresholds. | Treat automatic seeds as calibration truth or fit to a reviewed subset. | Generated controls can contain independent defects. Source-group holdout prevents a mutation from leaking a control into the other split. | `test_calibration_and_localization_are_independent`. |
| Keep unknown span axes out of localization denominators. | Infer whether a bare label points to source or candidate, or use its category alone. | Guessing an axis can create false overlap. We keep category evidence and report localization coverage. | `pairs` and `measure` in [calibration.py](src/bodhan_eval/calibration.py). Confirm the external convention with Bodhan. |
| Cache at the paid request boundary. | Cache final reports or keep exact model responses. | Response reuse permits rescoring, label changes, and comparison without repeating identical calls. Fresh reliability trials bypass the cache. | [Provider cache](src/bodhan_eval/providers.py) and the opt-in live replay test. |
| Account for Bodhan spend with integer micro-rupees. | Derive spend after dispatch or reserve before each request. | Budget checks include concurrent calls and conservative reservations after ambiguous billing. The judge is billed by its configured provider and reported in tokens when price is unavailable. | `Providers._request` and structured run logs. Confirm expected cap scope with Bodhan. |
| Report incomplete work explicitly. | Collapse skipped checks into a clean verdict or expose coverage. | A missing modality, exhausted budget, or unavailable judge is visible. The diff marks changed coverage separately. | `test_cli_ten_real_input_cases_without_credentials`. |
| Use one checked-in dataset of original English sources. | Commit generated media or publish generated results separately. | The reviewer can regenerate the content with their own keys. Keep audio outside Git and link the verified bundle with the demo. | [Demo sources](data/demo.jsonl) and [injector](src/bodhan_eval/demo.py). |
| Follow both package contracts. | Change Cookbook validation or document its generic key alongside the assignment-specific config. | The package reads the brief's per-model keys and the Cookbook can validate the recipe layout. Dependency pins live in `requirements.txt` and supply `pyproject.toml`. | [.env.example](.env.example), [package metadata](pyproject.toml), and Cookbook validation. |

## Verification before live credentials

The recipe's 12 tests pass on Windows and in the Linux image. The Cookbook recipe validator and repository secret scan pass. The opt-in hosted test remains pending.

The Linux `make check` run passes 31 Cookbook tests and fails the all-recipes test. The current validator does not recognize the AndroidSpeak source layout and reports missing environment setup for that example. The same test fails on the untouched upstream base `ae51852`.

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
