# Design notes

## Tool granularity

Ten tools wrap one capability each; three more handle plumbing between them. I chose **high-level
tools that hide each model's limits**, not one thin tool per API endpoint, because the model should
spend its steps on the task and not on chunking audio or splitting text.

| Limit | Handled inside |
|---|---|
| Transcribe: 30 s per request, WAV preferred | `transcribe_audio` converts with ffmpeg and cuts at silences into chunks of 28 s or less |
| Translate: output can be cut off (`truncated`) | `translate_text` splits by paragraph, keeps tables whole by rows, retries cut-off pieces in halves |
| Speak: about 30 s per request, may repeat or omit | `speak_text` splits into sentences and joins the audio; `verify_speech` checks the result |
| OCR: images only, one page per request, big tables can return `502` | `ocr_document` renders PDF pages and retries with a larger token limit |

## Media plumbing

Audio, video and PDFs are always passed **by path**; results come back as small dicts. Text longer
than 3,000 characters is saved to a file and returned as `text_file` plus a preview; every tool
accepts `text_file` as input, and `read_text` lets the model read a slice when it must summarise.
This keeps large data out of the model's context and avoids retyping errors.

## Unknown source language

`transcribe_audio` can run without a language. `detect_text_language` reads the **script** of the
result. A script can narrow the language down but not always decide it (Devanagari could be Hindi,
Marathi, Nepali and others), so the tool returns the candidates and marks `ambiguous`. The agent is
told to record the assumption in its report. It does not guess silently.

## Recovery

* Network errors, `429` and `5xx` are retried with exponential backoff and `Retry-After`.
* Other errors are not retried; they return a structured error (`code`, `retryable`, `hint`).
* A `401` right after a success on the same key is retried once (the docs call these transient).
* If `/translate` returns `404` the client falls back to the chat route and remembers that.
* Odd `200` responses (empty body, `null`) become `bad_response` errors instead of crashes.
* Empty or non-image files are rejected before any request is sent.

## Cost

* Every result is cached on disk by a hash of the request, so identical inputs are never paid for twice.
* The whole job's cost is checked against the budget **before** the first paid call.
* Rupees are tracked per model and shown in the trace and report.

## Prompt injection

The defence is architectural first: the model has no tool that sends data anywhere or writes outside
the workspace, and text from files is marked as untrusted data in every result. A phrase detector
flags instruction-like text in the trace and report; it is a warning, not a guarantee.

## Impossible tasks

`language_support` and the tools' own checks reject unsupported languages before any API call.
French is supported by none of the four models, so "dub this video into French" finishes as
`impossible`, with no output files and no money spent.

## Trade-offs and limits

* Translation quality is checked only by script (`script_ok`) and length ratio, not by meaning.
  A back-translation check would be the next step.
* Only generated speech is verified automatically. OCR accuracy is not independently checked.
* The injection detector is a heuristic; a determined attacker could phrase text to avoid it.
* Calls run one at a time. Parallel calls would speed up long jobs but were left out for reliability.
* Tested end to end with a free-tier LLM, whose per-minute token limits cause waits; the client backs off.
* Not every language and voice has been tried live; most checks run against a faked API.