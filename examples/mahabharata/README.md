# 🚀 [▶ WATCH THE Video (Download): VOICE → REASONING → CHARTS → VOICE](https://github.com/ninth-orbit-research/Bodhan-AI-Cookbook/blob/recipe/mahabharata/examples/mahabharata/Bodhan%20API%20Project%20%282%29.mp4)

# Bodhan AI Cookbook

**Production patterns for voice-native, multilingual AI in 22 Indian languages: five APIs, one key, no glue code.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![validate](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/actions/workflows/validate.yml/badge.svg)](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/actions/workflows/validate.yml)
[![API reference](https://img.shields.io/badge/API-reference-orange.svg)](https://console.bodhan.ai/api-docs/)
![Models](https://img.shields.io/badge/Bodhan%20models-5-8A2BE2)
![Retrieval](https://img.shields.io/badge/retrieval-vectorless%20graph%20RAG-0b7285)
![Tools](https://img.shields.io/badge/LLM%20chart%20tools-24-e8590c)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)

Example code, guides, and end-to-end projects for building with the [Bodhan AI API](https://console.bodhan.ai/api-docs/): speech to text, text to speech, translation, transliteration, and document OCR, with first-class support for Indian languages.

This cookbook goes past single API calls. Its flagship example is a complete **voice → reasoning → visualization → voice** system. Ask a question out loud in Hindi or Telugu. An agent navigates a knowledge graph without a vector database, decides for itself which analytical tools to run, draws the charts, and answers back in your language.

> **Speak** in your language → **understand** → **reason** over a knowledge graph → **execute tools** → **visualize** → **speak back**. Three Bodhan models carry the language work. Everything between them is plain Python.

| | |
|---|---|
| **Languages demonstrated** | English · Hindi · Telugu (the same pipeline serves every language Bodhan supports) |
| **Language layer** | `indic-transcribe` (speech in) · `indic-translate` (into English, and back out) · `indic-speak` (speech out) |
| **Reasoning** | Groq-hosted GPT-OSS family, with LLM-driven tool calling |
| **Retrieval** | Vectorless Graph RAG: no embeddings, no vector store, no index to rebuild |
| **Execution** | 24 predefined chart tools chosen and parameterized by the model |
| **Outputs** | Spoken answer · written answer · interactive figures · full retrieval trace |

## System architecture

```mermaid
flowchart TB
    subgraph EDGE["① INGRESS · any language, any modality"]
        direction LR
        I1["🎙️ Speech<br/>WAV"]
        I2["⌨️ Text<br/>native · roman · code-mixed"]
    end

    subgraph BIN["② BODHAN · language in"]
        direction LR
        B1["indic-transcribe<br/>25 languages"]
        B2["indic-translate<br/>→ English<br/>paragraph-aware chunking"]
        B1 --> B2
    end

    subgraph CORE["③ REASONING CORE · English only, language-agnostic"]
        direction TB
        subgraph RAG["Vectorless Graph RAG"]
            direction LR
            R1["Navigator LLM<br/>picks graph keys by name"]
            R2["Bounded walk<br/>3 branches / level<br/>100-node budget"]
            R3["Context assembly<br/>path-tagged leaves<br/>30k-char cap"]
            R1 --> R2 --> R3
            R2 -. "next level" .-> R1
        end
        subgraph TOOLS["Dynamic tool-based visualization"]
            direction LR
            T1["Tool schemas<br/>auto-generated from<br/>function signatures"]
            T2["Planner LLM<br/>tool_calls + JSON args"]
            T3["Chart registry<br/>24 chart types"]
            T4["Per-chart error<br/>isolation"]
            T1 --> T2 --> T3 --> T4
        end
        RAG ==>|"grounded context"| TOOLS
    end

    subgraph BOUT["④ BODHAN · language out"]
        direction LR
        O1["indic-translate<br/>English → user language"]
        O2["indic-speak<br/>45 voices · 14 styles"]
        O1 --> O2
    end

    subgraph EGRESS["⑤ EGRESS"]
        direction LR
        E1["🔊 Spoken answer"]
        E2["📝 Written answer"]
        E3["📊 Interactive Plotly figures<br/>+ retrieval trace"]
    end

    I1 --> B1
    I2 --> B2
    B2 ==> R1
    TOOLS ==>|"answer text"| O1
    O2 --> E1
    O1 --> E2
    T4 ==>|"figures"| E3
```

The design principle: **language is a boundary, not a dependency.** Translation wraps the reasoning core on both sides, so retrieval, prompts, tools and charts are written once and serve every language Bodhan supports.

### Why this is more than RAG

| Typical RAG chatbot | This pipeline |
|---|---|
| English text in, English text out | Voice in and voice out, in the user's own language |
| Embeds chunks and ranks by similarity | **Navigates a graph by reasoning**, so every answer has a path you can audit |
| Answers in prose | **Calls tools**: the model decides a sankey, a treemap or a line chart explains the answer better, and fills in the data |
| Numbers can drift in a paragraph | Figures come from retrieved context, and anything the model supplies itself is tagged `(approx.)` |
| One failure fails the request | Each chart, retry and translation chunk degrades on its own |

## Contents

- [Models at a glance](#models-at-a-glance)
- [Deep dive: the engine](#deep-dive-the-engine)
  - [Request lifecycle](#request-lifecycle)
  - [Vectorless graph RAG](#vectorless-graph-rag)
  - [Dynamic tool-based visualization](#dynamic-tool-based-visualization)
  - [Resilience by design](#resilience-by-design)
  - [Run it](#run-it)
  - [Known limitations](#known-limitations)
- [More pipelines](#more-pipelines)
- [Getting started](#getting-started)
- [Repository layout](#repository-layout)
- [API tutorials](#api-tutorials)
- [Example projects](#example-projects)
- [Integrations](#integrations)
- [Quality gates](#quality-gates)
- [Contributing](#contributing)
- [Resources](#resources)
- [License](#license)

## Models at a glance

| Model | Capability | Endpoint | Languages |
|---|---|---|---|
| `indic-transcribe` | Speech to text | `POST /v1/audio/transcriptions` | 25 Indian languages + English |
| `indic-translate` | Translation | `POST /translate` | 22 Indian languages + English, native / roman / code-mixed output |
| `indic-transliterate` | Transliteration | `POST /transliterate` | 22 Indic languages, native ⇄ roman |
| `indic-speak` | Text to speech | `POST /v1/audio/speech` | 22 Indian languages + English, 45 voices, 14 styles |
| `indic-ocr` | Document OCR | `POST /v1/chat/completions` | script-agnostic, Markdown + layout blocks |

Base URL `https://api.bodhan.ai`, header `Authorization: Bearer $BODHAN_API_KEY`. Model ids are lower-case and case-sensitive. The machine-readable version of this table, including every language code and voice, is [`scripts/bodhan_api_rules.json`](scripts/bodhan_api_rules.json).

**Drop-in compatible.** The `/v1` routes accept OpenAI-style requests, so the official [OpenAI SDKs](integrations/openai_sdk/) work after a one-line `base_url` change.

```bash
export BODHAN_API_KEY=<your API key>   # https://console.bodhan.ai

curl https://api.bodhan.ai/translate \
  -H "Authorization: Bearer $BODHAN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "The meeting has been postponed.", "target_language": "kn"}'
```

## Deep dive: the engine

Source: [`examples/mahabharata/`](examples/mahabharata/) (the directory name is the sample dataset; the engine is dataset-agnostic and ships with a second graph to prove it: a one-line env switch, `KNOWLEDGE_GRAPH`).

### Request lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant TR as Bodhan<br/>indic-transcribe
    participant TL as Bodhan<br/>indic-translate
    participant NAV as Navigator LLM
    participant KG as Knowledge graph<br/>(nested JSON)
    participant PLN as Planner LLM
    participant REG as Chart registry
    participant SP as Bodhan<br/>indic-speak

    U->>TR: audio (any supported language)
    TR-->>TL: transcript
    TL-->>NAV: English query

    loop each level, until leaf or budget exhausted
        NAV->>KG: list child keys
        KG-->>NAV: keys
        NAV-->>NAV: select ≤ 3 most relevant (JSON array, validated against real keys)
    end
    KG-->>PLN: path-tagged context (capped)

    PLN->>PLN: ground facts → numbers, pick chart type(s)
    PLN->>REG: tool_calls [{name, JSON args}, …]
    loop each requested chart
        REG-->>REG: build_figure(tool, args)
    end
    REG-->>PLN: figures · or isolated per-chart errors
    PLN-->>TL: written answer (English)
    TL-->>SP: answer in user's language
    SP-->>U: spoken answer + figures + retrieval trace
```

### Vectorless graph RAG

Conventional RAG embeds chunks and ranks by cosine similarity. This pipeline has **no embeddings, no vector store, no index to rebuild**. The knowledge base is a nested JSON graph, and an LLM navigates it the way a person uses a table of contents: at each level it reads the child key names and chooses where to go next.

```mermaid
flowchart TD
    Q(["English query"]) --> W0["Walk(node = graph root, budget = 100)"]
    W0 --> BUD{"budget > 0?"}
    BUD -- "no" --> LIM["trace: limit<br/>stop descending"]
    BUD -- "yes" --> DEC["budget −= 1"]
    DEC --> KIND{"node type?"}

    KIND -- "dict (branch)" --> SEL["Navigator LLM<br/>query + child key names<br/>→ JSON array of keys"]
    SEL --> VAL["Validate: keep only exact<br/>existing keys · dedupe · cap at 3<br/>malformed JSON → []"]
    VAL --> TB["trace: branch<br/>{path, depth, options, selected}"]
    TB --> REC["Recurse into each selected key"]
    REC --> W0

    KIND -- "list / str (leaf)" --> LEAF["Collect text"]
    LEAF --> TLF["trace: leaf<br/>{path, depth, chars}"]
    TLF --> CTX["Append<br/>GRAPH PATH: A > B > C<br/>INFORMATION: …"]

    LIM --> OUT
    CTX --> OUT["Join leaves · truncate to 30k chars<br/>return context + full trace + elapsed_ms"]
```

| Property | Value | Why it matters |
|---|---|---|
| Index / embeddings | none | Edit a JSON file and retrieval changes instantly; nothing to re-embed |
| Branching factor | ≤ 3 keys per level | Bounds fan-out while keeping multi-hop answers |
| Node budget | 100 nodes per question | Hard cost and latency guard |
| Context cap | 30,000 chars (12,000 for the chart call) | Keeps the downstream prompt small |
| Navigator decoding | `temperature=0`, `top_p=0.2` | Repeatable traversal |
| Key validation | LLM output filtered to exact existing keys | A hallucinated key can't derail the walk |
| Provenance | every leaf tagged `GRAPH PATH: A > B > C` | Answers are traceable to named nodes |
| Observability | full `trace` of branch / leaf / limit events | The walk is inspectable and replayable |

### Dynamic tool-based visualization

The LLM never emits chart code and never sees a chart library. It sees **function-calling schemas**, and those schemas are generated at import time by introspecting the real chart functions, so adding a chart means writing one Python function.

```mermaid
flowchart LR
    subgraph BUILD["Import time · runs once"]
        direction TB
        F["Chart function<br/>bar_chart(x, y, mode=…, …)"] --> INS["inspect.signature()"]
        INS --> MAP["Map each parameter to JSON Schema<br/>numbers[] · strings[] · enum · bool · int"]
        MAP --> REQ["No default → required<br/>Code-owned args hidden:<br/>show · save · width · height · theme"]
        REQ --> SPEC["OpenAI-style tool spec<br/>name · docstring · parameters"]
    end

    subgraph RUN["Request time"]
        direction TB
        CTX["Retrieved graph context"] --> LLM["Planner LLM<br/>tool_choice = auto"]
        SPEC -. "TOOLS" .-> LLM
        LLM --> CALLS["tool_calls<br/>[(name, JSON args), …]"]
        CALLS --> CLEAN["Drop nulls · drop hidden args"]
        CLEAN --> EXEC["REGISTRY[name](**args)"]
        EXEC --> FIG["Plotly figure"]
        EXEC -. "bad args" .-> ERR["Isolated error<br/>other charts still render"]
    end

    BUILD ==> RUN
```

**24 chart tools, 6 families**, all selected by the model from the question and the retrieved data:

| Family | Tools |
|---|---|
| Trend | `line` · `area` · `band` |
| Comparison | `bar` · `lollipop` · `waterfall` · `pareto` |
| Distribution | `histogram` · `box` · `violin` |
| Relationship | `scatter` · `bubble` · `heatmap` · `correlation` |
| Composition | `pie` · `donut` · `treemap` · `sunburst` · `funnel` |
| Specialty and flow | `candlestick` · `gauge` · `radar` · `sankey` · `gantt` |

Grounding rules are enforced in the prompt contract, not left to chance:

- Use names and numbers **exactly as in the retrieved context**; never invent figures.
- Where sources differ, say so.
- If the model has to supply data from its own knowledge, the title is tagged **`(approx.)`**.
- Every request also yields a plain-language written answer, so charts never arrive without explanation.

### Resilience by design

```mermaid
flowchart LR
    subgraph KEYS["Round-robin key pool"]
        direction TB
        K1["Thread-safe cyclic pointer"] --> K2["Fresh client per call<br/>next key in the cycle"]
        K2 --> K3["Keys from .env only<br/>deduplicated, never hard-coded"]
    end

    subgraph RL["Rate-limit handling"]
        direction TB
        R1["429 from LLM"] --> R2["Backoff + retry<br/>navigator: 4 attempts<br/>planner: 5 attempts"]
        R2 --> R3["Optional answer call limited?<br/>keep the charts anyway"]
    end

    subgraph LANG["Language safety"]
        direction TB
        L1["Language code validated<br/>before any network call"] --> L2["English in/out short-circuits<br/>no wasted translate hop"]
        L2 --> L3["Long text split on paragraph<br/>boundaries, translated per chunk,<br/>rejoined in order"]
        L3 --> L4["TTS input stripped of markup<br/>and length-capped"]
    end

    subgraph FAIL["Graceful degradation"]
        direction TB
        F1["Bad tool arguments"] --> F2["That chart fails alone<br/>error returned, rest render"]
        F2 --> F3["No chart chosen →<br/>text answer still returned"]
    end
```

### Run it

```bash
cd examples/mahabharata
pip install -r requirements.txt
```

Add two keys to a `.env` file in that folder (never commit it): `BODHAN_API_KEY` from the [Bodhan console](https://console.bodhan.ai), and `GROQ_API_KEY` from the [Groq console](https://console.groq.com). To spread load, add more Groq keys as a comma-separated `GROQ_API_KEYS`, and the key pool rotates through them. Then:

```bash
bash run.sh
```

To try the retrieval and tool-calling core without audio, run `python llm.py "your question"`. It prints the chosen tools, their JSON arguments, and whether each chart built.

### Known limitations

- Answer quality is bounded by the completeness and accuracy of the knowledge graph. Retrieval can only find what the graph contains.
- Translation quality varies across Indic languages. Check output in the languages you ship.
- LLM interpretations and statistics should be checked against source material. Grounding rules reduce invented figures; they don't eliminate the need to verify.
- API availability, rate limits and inference cost depend on the Bodhan and Groq plans you use.
- Multi-hop questions cost more: each graph level is an LLM call, and each chart is a tool execution.

## More pipelines

### Announce once, reach everyone

[`examples/voice_notice_translator`](examples/voice_notice_translator/) turns one spoken announcement into five spoken announcements.

```mermaid
flowchart LR
    V["🎙️ One spoken notice"] --> T["indic-transcribe"]
    T --> TR{"indic-translate<br/>fan-out ×5"}
    TR --> L1["Language 1"] --> S1["indic-speak 🔊"]
    TR --> L2["Language 2"] --> S2["indic-speak 🔊"]
    TR --> L3["Language 3"] --> S3["indic-speak 🔊"]
    TR --> L4["Language 4"] --> S4["indic-speak 🔊"]
    TR --> L5["Language 5"] --> S5["indic-speak 🔊"]
```

### Two-way conversation on a phone

[`examples/AndroidSpeak`](examples/AndroidSpeak/) (Saathi) is a native Android app. Two microphone buttons, one per language; the same three-model chain runs in whichever direction you tap.

```mermaid
flowchart LR
    subgraph A["Tap mic A"]
        direction LR
        A1["Speak language A"] --> A2["indic-transcribe"] --> A3["indic-translate<br/>A → B"] --> A4["indic-speak<br/>plays in B"]
    end
    subgraph B["Tap mic B"]
        direction LR
        B1["Speak language B"] --> B2["indic-transcribe"] --> B3["indic-translate<br/>B → A"] --> B4["indic-speak<br/>plays in A"]
    end
```

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

Most recipes are Python 3.10+ notebooks, but the API is plain HTTPS: any language with an HTTP client works.

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

| Project | Bodhan models | What it demonstrates |
|---|---|---|
| [Voice-to-chart agent](examples/mahabharata/) | transcribe · translate · speak | Vectorless graph RAG, LLM tool calling over 24 chart types, translation as a boundary around an English-only core, key pooling and rate-limit resilience |
| [Voice Notice Translator](examples/voice_notice_translator/) | transcribe · translate · speak | Transcribes a spoken announcement, translates it into five languages, and reads each one aloud in a native voice |
| [Saathi (Android)](examples/AndroidSpeak/) | transcribe · translate · speak | Native two-way voice translator with a replayable conversation log and a typed-input fallback |
| [TEMPLATE](examples/TEMPLATE/) | n/a | Starting point for new recipes: README skeleton, notebook, pinned requirements, `.env.example` |

Ideas waiting for a contributor: exam paper reader (OCR → translate), multilingual customer-feedback analyser, WhatsApp-style roman-script input box, lecture subtitler, government-scheme summariser, IVR voice agent. Open a [recipe proposal](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook/issues/new?template=recipe_proposal.md) to claim one.

## Integrations

| Integration | Description |
|---|---|
| [OpenAI SDK](integrations/openai_sdk/) | Using Bodhan's OpenAI-compatible `/v1` routes through the official `openai` Python and Node packages |

LiveKit, Pipecat, Twilio/Exotel, n8n, Vercel AI SDK and LangChain guides are open for contribution; see [`integrations/README.md`](integrations/README.md).

## Quality gates

Every recipe is machine-checked before it merges, so what you clone is what runs.

```mermaid
flowchart LR
    PR["Pull request"] --> CI["GitHub Actions<br/>validate.yml"]
    CI --> C1["🔐 Secret-leak scan"]
    CI --> C2["📁 Recipe structure"]
    CI --> C3["🧹 Notebook outputs cleared"]
    CI --> C4["🏷️ Model-id compliance<br/>vs bodhan_api_rules.json"]
    C1 & C2 & C3 & C4 --> OK{"All pass?"}
    OK -- yes --> M["Ready to merge"]
    OK -- no --> F["Fix and push"]
    F --> CI
```

The model allowlist is a single JSON file, [`scripts/bodhan_api_rules.json`](scripts/bodhan_api_rules.json), updated from [console.bodhan.ai/api-docs](https://console.bodhan.ai/api-docs/). A recipe that calls a retired or misspelled model id fails CI instead of failing for your users.

## Contributing

We welcome new examples and fixes. Before opening a pull request:

1. Read [CONTRIBUTING.md](CONTRIBUTING.md) for the recipe layout, security requirements and API standards.
2. Scaffold from the template: `make new-recipe NAME=my_recipe`.
3. Run local validation with `make check`.

## Resources

- [Bodhan API reference](https://console.bodhan.ai/api-docs/)
- [Bodhan console](https://console.bodhan.ai): keys, usage and billing
- [Bodhan.AI](https://bodhan.ai)
- [Bodhan on GitHub](https://github.com/Bodhan-AI)

## License

This repository is licensed under the [Apache License 2.0](LICENSE).

---

<p align="center"><b>Bodhan AI × Groq × Vectorless Graph RAG × LLM tool calling</b><br/><i>Voice-native AI for every Indian language.</i></p>
