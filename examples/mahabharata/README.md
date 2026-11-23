# 🚀 [▶ WATCH THE MAHABHARATA AI DEMO](https://github.com/ninth-orbit-research/Bodhan-AI-Cookbook/blob/recipe/mahabharata/examples/mahabharata/Bodhan%20API%20Project%20%282%29.mp4)

# 🕉️ Mahabharata — Cognitive AI with Tool-Based Execution

**An intelligent, multilingual exploration of the Mahabharata powered by Vectorless Graph RAG, LLM reasoning, and dynamic visualizations.**

> Explore the Mahabharata through natural-language conversations, multilingual voice interaction, knowledge graph navigation, and AI-powered statistical analysis.

---

### ✨ Project Overview

| Component | Technology |
|---|---|
| LLM | GPT-OSS 120B (Groq API) |
| Retrieval | Vectorless Graph RAG |
| Translation | Bodhan `indic-translate` |
| Speech Recognition | Bodhan `indic-transcribe` |
| Speech Synthesis | Bodhan `indic-speak` |
| Execution | LLM-driven tool calling |
| Visualization | Dynamic charts and analytical tools |
| Languages | English, Hindi, Telugu |

### 🌟 What Makes It Different?

Mahabharata is an interactive AI-powered exploration system that enables users to investigate the epic through natural-language questions in their preferred language.

The architecture combines Bodhan AI's multilingual capabilities with Groq-hosted GPT-OSS 120B to perform reasoning, navigate a knowledge graph, retrieve contextual information, and execute predefined tools.

Beyond conventional RAG, the system supports **tool-based analytical execution**, allowing the LLM to generate charts and visualizations to answer statistical and data-driven questions about the Mahabharata.

### 🔥 Key Features

- **Multilingual Interaction:** Communicate through voice or text in English, Hindi, and Telugu.
- **Vectorless Graph RAG:** Navigate structured knowledge graphs without conventional vector similarity retrieval.
- **LLM Reasoning:** Use GPT-OSS 120B to interpret queries and determine execution strategies.
- **Tool-Based Execution:** Dynamically select and invoke predefined functions.
- **Dynamic Visualization:** Generate charts and statistical representations to explain complex questions.
- **Voice Interaction:** Convert speech to text and AI-generated responses back into speech.
- **Contextual Exploration:** Explore characters, relationships, historical narratives, and events.

### ⚙️ How It Works

1. **Voice / Text Input:** Users submit questions in their preferred language.
2. **Speech Recognition:** Bodhan's `indic-transcribe` converts spoken queries into text.
3. **Input Translation:** Bodhan's `indic-translate` translates queries into English.
4. **LLM Reasoning:** GPT-OSS 120B interprets the question and selects an execution strategy.
5. **Graph Navigation:** Vectorless Graph RAG retrieves relevant contextual information.
6. **Tool Execution:** The LLM selects predefined tools for analytical operations.
7. **Dynamic Visualization:** Charts and visualizations communicate statistical insights.
8. **Response Generation:** The LLM synthesizes graph context and tool outputs.
9. **Multilingual Output:** Bodhan translates the response and generates speech using `indic-speak`.

### 🛠️ Installation

```bash
cd examples/mahabharata
pip install -r requirements.txt
```

### 🔑 API Configuration

Obtain your API keys:

- [Bodhan AI Console](https://console.bodhan.ai)
- [Groq Console](https://console.groq.com)

Configure the required API keys in your environment.

### ▶️ Run the Application

```bash
bash run.sh
```

### ⚠️ Limitations

- Response quality depends on the completeness and accuracy of the underlying Mahabharata knowledge graph.
- Translation quality may vary across Indic languages.
- LLM-generated interpretations and statistical conclusions should be verified against source material.
- API availability, rate limits, and inference costs depend on the respective service providers.
- Complex queries may require additional graph traversal and tool execution time.

---

### 💡 Built With

**Bodhan AI × Groq × GPT-OSS 120B × Vectorless Graph RAG**

*Bringing ancient knowledge into the age of Cognitive AI.*
