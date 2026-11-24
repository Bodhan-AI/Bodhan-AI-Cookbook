# Unsupported French MP4 — corrected negative test (23 November 2026)

**Task:** Translate this video into French and save a French output video.

## First observed attempt — incorrect reasoning

The run stopped without model calls or fabricated artifacts but the planning LLM incorrectly claimed that `translate_text`, `translate_segments` and `speak_text` support French. It blamed the lack of video muxing instead of rejecting French as an unsupported target. The generic `REQUIRED_OUTPUT_MISSING` message also mentioned OCR even though no OCR was involved. This refusal was not considered a fully correct negative test.

## Root cause and fix

The LLM saw only examples of supported languages, not the complete registry; model claims were not authoritative. We added **conservative generic target-language preflight validation** backed by the existing `bodhan_shared.languages.normalize_lang` registry, before planning. For explicitly unsupported output targets, the agent reports `UNSUPPORTED_LANGUAGE` and no fabricated deliverables. This is not hardcoded for French, videos or any particular demo task. Ambiguous requests still go to the planning agent and its validated tool schemas.

## Corrected observed result

- Status `not_completed` — expected and appropriate
- Error: `UNSUPPORTED_LANGUAGE`; the message explicitly states Bodhan Indic-Translate and Indic-Speak do not support French
- Steps 0; 0 tool/model calls; 0 generated artifacts; estimated Bodhan charge ₹0.00
- `trace.jsonl` records the MP4 input and a non-success finish event, with no tool call
- The output report shows elapsed time `0.0` because the early refusal finishes almost immediately (rounded)

**Known limitation:** Only explicit unsupported target-language phrases are rejected before planning. The model is still responsible for ambiguous task interpretation. No claim is made about being able to translate/synthesize French through external providers.
