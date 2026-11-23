# Mahabharata

A multilingual, AI-powered Mahabharata exploration system combining Vectorless Graph RAG, LLM reasoning, and dynamic visualization to enable users to explore the epic through natural-language questions.

**Bodhan models:** `indic-translate`, `transcribe`, `text-to-speech` (and other models only if implemented)
**Languages shown:** English, Hindi, Telugu
**Format:** Interactive AI application

## What it does

Mahabharata is an interactive AI-powered exploration system that allows users to ask questions about the Mahabharata in their preferred language.

The system combines Bodhan AI's translation capabilities with Groq-hosted GPT-OSS 120B to perform Vectorless Graph RAG, retrieve contextual information, and execute predefined tools. It can also generate dynamic charts and visualizations to explain statistical and analytical questions about the Mahabharata.

Users can explore historical narratives, characters, relationships, events, and statistical insights through a conversational interface.

## Demo

Watch the complete working demonstration:

https://github.com/user-attachments/assets/REPLACE_WITH_VIDEO_ID

## Setup

```bash
cd examples/mahabharata
pip install -r requirements.txt
```

API keys:
- Bodhan: https://console.bodhan.ai
- Groq: https://console.groq.com

## Run

```bash
bash run.sh
```

## How it works

1. **Multilingual Voice Input:** Users can ask questions through voice or text in supported Indic languages.
2. **Speech Recognition:** Bodhan's `indic-transcribe` converts spoken queries into text.
3. **Language Translation:** Bodhan's `indic-translate` converts the input into English for LLM processing.
4. **LLM Reasoning:** Groq-hosted GPT-OSS 120B interprets the query and determines the appropriate execution strategy.
5. **Vectorless Graph RAG:** The system navigates the knowledge graph to retrieve relevant contextual information without conventional vector similarity retrieval.
6. **Tool-Based Execution:** The LLM selects and invokes predefined tools to perform analytical operations and generate dynamic visualizations.
7. **Response Generation:** The LLM synthesizes retrieved information and tool outputs into a contextual response.
8. **Multilingual Translation:** Bodhan's `indic-translate` converts the generated response into the user's preferred language.
9. **Speech Synthesis:** Bodhan's `indic-speak` converts the translated response into natural speech, enabling voice-based interaction.

## Limitations

- Response quality depends on the completeness and accuracy of the underlying Mahabharata knowledge graph.
- Translation quality may vary across Indic languages.
- LLM-generated interpretations and statistical conclusions should be verified against source material.
- API availability, rate limits, and inference costs depend on the respective service providers.
- Longer or more complex queries may require additional graph traversal and tool execution time.

---