# Bodhan AI Cookbook

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![validate](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/actions/workflows/validate.yml/badge.svg)](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/actions/workflows/validate.yml)
[![API reference](https://img.shields.io/badge/API-reference-orange.svg)](https://console.bodhan.ai/api-docs/)

Example code, guides, and end-to-end projects for building with the [Bodhan AI API](https://console.bodhan.ai/api-docs/): speech to text, text to speech, translation, transliteration, and document OCR, with first-class support for Indian languages.

## Contents

- [Getting started](#getting-started)
- [Models at a glance](#models-at-a-glance)
- [Repository layout](#repository-layout)
- [API tutorials](#api-tutorials)
- [Example projects](#example-projects)
- [Integrations](#integrations)
- [Contributing](#contributing)
- [Resources](#resources)
- [License](#license)

## Getting started

You need a Bodhan API key. Sign up at [console.bodhan.ai](https://console.bodhan.ai) to get one.

Set it as an environment variable:

```bash
export BODHAN_API_KEY=<your API key>
```

Or create a `.env` file in the recipe folder (see each recipe's `.env.example`):

```plaintext
BODHAN_API_KEY=<your API key>
```

A first call, from the shell:

```bash
curl https://api.bodhan.ai/translate \
  -H "Authorization: Bearer $BODHAN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "The meeting has been postponed.", "target_language": "kn"}'
```

Most recipes are Python 3.10+ notebooks, but the API is plain HTTPS: any language with an HTTP client works, and the `/v1` routes accept OpenAI-style requests so the [OpenAI SDKs](integrations/openai_sdk/) work with a `base_url` change.

## Models at a glance

| Model | Capability | Endpoint | Languages |
|---|---|---|---|
| `indic-transcribe` | Speech to text | `POST /v1/audio/transcriptions` | 25 Indian languages + English |
| `indic-translate` | Translation | `POST /translate` | 22 Indian languages + English, native / roman / code-mixed output |
| `indic-transliterate` | Transliteration | `POST /transliterate` | 22 Indic languages, native ⇄ roman |
| `indic-speak` | Text to speech | `POST /v1/audio/speech` | 22 Indian languages + English, 45 voices, 14 styles |
| `indic-ocr` | Document OCR | `POST /v1/chat/completions` | script-agnostic, Markdown + layout blocks |

Base URL `https://api.bodhan.ai`, header `Authorization: Bearer $BODHAN_API_KEY`. Model ids are lower-case and case-sensitive. The machine-readable version of this table, including every language code and voice, is [`scripts/bodhan_api_rules.json`](scripts/bodhan_api_rules.json).

## Repository layout

| Folder | Contents |
|---|---|
| [`getting-started/`](getting-started/) | Focused, single-API tutorial notebooks: speech to text, text to speech, translation, transliteration, document OCR |
| [`examples/`](examples/) | Complete example projects and apps built on the Bodhan API. Start new recipes from [`examples/TEMPLATE/`](examples/TEMPLATE/) |
| [`integrations/`](integrations/) | Guides for using Bodhan with third-party SDKs, frameworks and platforms |
| [`sample_data/`](sample_data/) | Small shared inputs used by the tutorials |
| [`scripts/`](scripts/) | CI validation tooling and the model allowlist (`bodhan_api_rules.json`) |
| [`tests/`](tests/) | Unit tests for the validation scripts |

## API tutorials

| Capability | Notebook | Covers |
|---|---|---|
| Speech to text | [speech_to_text.ipynb](getting-started/speech-to-text/speech_to_text.ipynb) | Transcribing a clip, language hints, chunking longer audio, error handling |
| Translation | [translate.ipynb](getting-started/translate/translate.ipynb) | Translating text, choosing native / roman / code-mixed output, fanning out to many languages, long documents |
| Transliteration | [transliterate.ipynb](getting-started/transliterate/transliterate.ipynb) | Native ⇄ roman script conversion, and how it differs from translation |
| Text to speech | [text_to_speech.ipynb](getting-started/text-to-speech/text_to_speech.ipynb) | Voices per language, speaking styles, joining clips for longer text |
| Document OCR | [document_ocr.ipynb](getting-started/document-ocr/document_ocr.ipynb) | OCR of a page image, layout blocks with bounding boxes, tables, multi-page PDFs |

## Example projects

| Project | Description |
|---|---|
| [Voice Notice Translator](examples/voice_notice_translator/) | Transcribes a spoken announcement, translates it into five languages, and reads each one aloud in a native voice |
| [TEMPLATE](examples/TEMPLATE/) | Starting point for new recipes: README skeleton, notebook, pinned requirements, `.env.example` |

Ideas waiting for a contributor: exam paper reader (OCR → translate), multilingual customer-feedback analyser, WhatsApp-style roman-script input box, lecture subtitler, government-scheme summariser, IVR voice agent. Open a [recipe proposal](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/issues/new?template=recipe_proposal.md) to claim one.

## Integrations

| Integration | Description |
|---|---|
| [OpenAI SDK](integrations/openai_sdk/) | Using Bodhan's OpenAI-compatible `/v1` routes through the official `openai` Python and Node packages |

LiveKit, Pipecat, Twilio/Exotel, n8n, Vercel AI SDK and LangChain guides are open for contribution; see [`integrations/README.md`](integrations/README.md).

## Contributing

We welcome new examples and fixes. Before opening a pull request:

1. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the recipe layout, security requirements and API standards.
2. Scaffold from the template: `make new-recipe NAME=my_recipe`.
3. Run local validation with `make check`.

CI checks every pull request for secret leaks, recipe structure, cleared notebook outputs, and model-id compliance. Current models are tracked in [`scripts/bodhan_api_rules.json`](scripts/bodhan_api_rules.json) and updated from [console.bodhan.ai/api-docs](https://console.bodhan.ai/api-docs/).

## Resources

- [Bodhan API reference](https://console.bodhan.ai/api-docs/)
- [Bodhan console](https://console.bodhan.ai) — keys, usage and billing
- [Bodhan.AI](https://bodhan.ai)
- [Bodhan on GitHub](https://github.com/Bodhan-AI)

## License

This repository is licensed under the [Apache License 2.0](LICENSE).
