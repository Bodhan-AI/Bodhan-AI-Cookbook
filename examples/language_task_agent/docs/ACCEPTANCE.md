# Current acceptance — November 21, 2026

[Current review evidence](REVIEW_RESULTS.md) · [Earlier acceptance history](history/ACCEPTANCE_through_20261121.md)

| Area | Observed result |
| --- | --- |
| Recipe regression suite | 158 tests pass locally; fake model transports, real media conversion and actual stdio MCP subprocesses |
| Linux portability | 158 tests and lint pass on arm64 and amd64; amd64 uses local emulation |
| Standalone MCP | Official MCP Python SDK drives the server, rejects bad calls and reads valid input afterward |
| Fresh declared tasks | Primary 11/13; alternate 7/11 including 3 timeouts and 1 free-quota refusal; separate fixes/clarification 2/2 |
| Natural Hindi | Full180s input processed;35cues,36recognition anchors,29passing translation checks; heading at9.880s; estimated phrase timing |
| Tamil | 115.456s MP3 within 115–125s; matching text/source and 9 audio-chunk checks pass |
| Recording | 255s narrated HyperFrames walkthrough; recorded PTY excerpts/cache use labeled; original97.84s capture preserved |
| Credentials/media | Actual environment files, caches and source media stay outside Git |
| Required disclosure | Codex assistance is stated in README and PR; candidate review is not invented |

The original six-case evidence and previous failures remain available. Earlier
acceptance notes are historical, not overrides of the current result. Lexical
verification and English-side examples are not native-language certification.
Hidden-task scores are unmeasured. Candidate review/rehearsal and the final email
reply remain separate. Upstream workflow approval/merging belongs to maintainers.

Exact Cookbook make check has the previously reproduced AndroidSpeak structural
failure on untouched upstream; recipe validation and root lint/rules are checked
separately. Repository-wide green is not claimed.
