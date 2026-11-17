# Design notes

## Tool boundaries

Tools are capability-level: each performs one Bodhan model operation and absorbs its media limits. Helpers expose file inventory, bounded text reads, language/voice support, and subtitle assembly. The planner composes those operations for unseen tasks.

## Reuse

LangGraph manages the plan-and-tool loop plus one verification repair. Planning and the first tool choice share one LLM request. LangChain turns typed Python functions into tools. The official MCP Python SDK exposes those same functions to other clients. httpx, diskcache, pypdfium2, and ffmpeg handle networking, repeat-call caching, PDF rendering, and media conversion.

## Model boundary

The LLM writes English plans, explanations, and summaries. Bodhan performs Indic OCR, transcription, translation, and speech. Text extracted from user files is always treated as untrusted input. No shell or arbitrary network tool is exposed to the agent.

## Failure and cost handling

Costs are estimated and reserved before calls. A successful response enters a private cache, so reruns with the same key and input avoid another paid request. Identical tool calls within a run return their first result. 429 and selected 5xx errors receive one bounded retry using Retry-After when available; rejected 429 responses release the reserved estimate. A dispatched timeout is reported without automatic retry and is charged conservatively because the provider may have processed it. Unsupported languages, missing input, invalid media, and exceeded budgets produce structured errors. Request attempts, failures, rate limits, and cost reservations are visible in the report.

## Verification

Every produced artifact is checked for existence and content. SRT output must contain valid cue timestamps. Translation files are checked for the requested writing system. Speech output is sent through Indic-Transcribe and compared with the exact source text; a failed check triggers at most one repair turn and remains visible in report.json.

## Limits

Speech and OCR calls can be expensive, so verification is budgeted too. Subtitle timing uses audio windows, not word timestamps. Script matching is a lightweight check and cannot establish translation quality. Native-speaker review remains the quality standard.

## What we took from the existing PRs

- [PR #19](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/pull/19) demonstrates detailed traces and budget reporting. We keep those reviewer-visible run records, while meeting the required stdio server without its extra transport surface.
- [PR #20](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/pull/20) has the clearest reusable capability boundary: the agent and MCP server call the same Python operations. We use that shared-core pattern and its early cost checks and structured errors.
- [PR #21](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/pull/21) emphasizes untrusted OCR/transcript content, provenance, and budget-safe caching. We keep the untrusted-data rule, local response cache, and tool result summaries; the cache key includes a digest of the selected model key.
- [PR #22](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/pull/22) presents a clear CLI and output-report layout. We use the required command shape, `result.md`, artifacts, trace, and JSON report, with a smaller explicit LangGraph loop.

The shared tool layer is deliberately a bit richer than one tool per API: file translation, transcription-to-translation, and translate-and-speak keep Indic content inside the Bodhan tool boundary so the planner does not need to handle it.
