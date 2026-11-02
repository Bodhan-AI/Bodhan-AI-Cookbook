# BhashaVoice SDK — Architecture

## 1. Document details

Version 0.4, 2 November 2026. Owner: Muhammad Umaid Rather.
Status: implemented local v0; review pending. Companion:
[SOFTWARE_SPECIFICATION.md](SOFTWARE_SPECIFICATION.md).

## 2. Overview and architectural drivers

Keep the SDK independent of Next.js and server credentials; preserve user text;
validate model-specific language capabilities once; ship a self-contained recipe.

## 3. System context

```text
Host React editor + BhashaVoice SDK
  | multipart recording / selected text
  v
Same-origin Next.js routes -> validation / bounded processing
  | FFmpeg WAV conversion                 | selected text
  v                                       v
Bodhan indic-transcribe              Bodhan indic-translate
  | transcript                             | translated text
  +------------> browser review / insert / replace
```

## 4. Containers and components

| Component | Responsibility |
| --- | --- |
| `packages/bhashavoice/src/languages.mjs` | Immutable speech/translation registries and validators; shared by SDK and backend |
| `CompactVoiceInput` and recorder/transcription/review hooks | Browser audio lifecycle, language selection, transcript actions and history |
| `SelectionTranslator` and selection helpers | Selection snapshot, target choice, card positioning and guarded replacement |
| `src/app/VoiceWorkspace.jsx` | Host-controlled textarea and saved-cursor insertion |
| `src/app/api/bhasha/*` | Same-origin Node route entry points |
| `src/server/audio/*` | Input validation, limits, temporary files and FFmpeg conversion |
| `src/server/bodhan/*` | Server authentication, provider transports, bounded parsing and safe errors |
| `src/index.d.ts` in SDK | Separate `TranscriptionLanguage` and `TranslationLanguage`; legacy `Language` alias |

The public SDK exports both language registries/guards and the two components.
`defaultTargetLanguage` chooses an initial target without source-script heuristics.

## 5. Bodhan API integration design

Speech posts `file`, `model=indic-transcribe`, `language` to
`/v1/audio/transcriptions`. Translation retains the live prototype's
`/v1/chat/completions` route with `model=indic-translate`, one user message and
`target_language_code`. No automatic transport fallback/retry duplicates paid work.
The provider infers source language. All supported translation variants remain
exact codes throughout the handler and SDK. No `hi-IN` locale substitution.

Speech and translation use separate immutable lists, verified against the API's
published enumeration and Cookbook machine-readable rules. Bhili/Bhojpuri are
speech-only. Translation adds Kashmiri/Manipuri/Sindhi script variants.

## 6. Data architecture

No database. Browser state holds editor contents, selection snapshots and five
recent voice inserts. Refresh clears that state. Audio is a Blob until upload;
server conversion uses private temporary storage and removes it in all exits.
Translation returns complete text plus requested target; raw provider payloads
are never exposed as host responses. Test fixtures use synthetic audio.

## 7. Key flows

- Recording -> Stop/25-second cap -> multipart validation -> bounded FFmpeg decode
  -> one provider call -> review -> host insertion callback.
- Selection -> explicit launcher click -> configured target -> one provider call
  -> compare -> replace only if original selection is unchanged.
- New target remounts translation work and cancels the previous request.
- Cancel/Discard/unmount abort work; stale results cannot replace newer state.

## 8. Security architecture

Server-only environment keys, no caching, input/body/response bounds, safe errors,
redirect rejection and per-process concurrency limits. Translation key precedence:
dedicated translation key, transcription key, common key. Speech prefers its
dedicated key then common key. No raw upstream logging. Browser requests use
same-origin credentials; cross-origin cookie/CORS policy belongs to the host.

## 9. Deployment and infrastructure

Development and recipe use Node 22.20+ and FFmpeg. Production needs a Node server;
static-only hosting cannot run the backend. A Docker service with FFmpeg is a
suitable demo deployment. Public hosting additionally needs host authentication
and shared usage limits. No deployed URL is claimed in this delivery.

The portable `examples/bhasha_voice/` snapshot has its own lockfile and workspace.
`npm run prepare:cookbook` copies explicitly tracked SDK/backend/tests/docs;
`npm run check:cookbook` verifies a fresh independent install/build/startup.
Real environment files, installed dependencies, builds and capture media are not
copied by synchronization.

## 10. Observability and operations

Safe error codes and safe request IDs identify provider failures without content
logging. Honor retry backoff. Record verification outcomes separately; live model
availability is not inferred from mocked tests. No analytics system is included.

## 11. Performance and scalability

Limits: 25 seconds / 5 MiB audio; translation <=2,000 UTF-16 code units;
20-second provider and 30-second handler deadlines; 64 KiB provider responses.
Two concurrent requests per endpoint per process. Multi-instance quotas need
shared infrastructure. No latency or linguistic accuracy SLA is claimed.

## 12. Testing and quality

Automated tests cover existing lifecycle/error/selection behaviors and every
published language code through host validation and provider request shaping.
Negative cases verify capability separation. The package consumer check exercises
the built tarball, public types, browser build and server import. The portable
recipe check runs an isolated install, tests, lint, build and credential-free
startup. Live checks and browser capture are documented separately in
[LANGUAGE_SUPPORT.md](LANGUAGE_SUPPORT.md) and [DEMO.md](DEMO.md).

## 13. Decision log

- One shared capability registry prevents frontend/backend drift.
- Separate types avoid advertising speech-only languages for translation.
- Keep `Language` alias for existing integrations.
- Explicit initial translation target replaces a Hindi/English script guess.
- Retain existing provider transport until measured live behavior justifies a change.
- Keep portable recipe generation to avoid divergent implementations.

## 14. Risks, technical debt and the path to V1

The user-reported microphone test and 2 November screen recording cover the
demonstrated local English flow; see [DEMO.md](DEMO.md). Additional browser/device
coverage, other live speech languages and native-speaker quality review remain.
Rich editors require adapters; iframe/shadow-root selection is unsupported.
No persistent history, comments, long recordings or streaming are implemented.
License, public hosting protections, deployment and npm publication remain separate.

## 15. Review

Maintainer and peer review pending; no approval is claimed. Check capability
coverage, source snapshots, privacy, fresh-clone setup and demo reproducibility.
