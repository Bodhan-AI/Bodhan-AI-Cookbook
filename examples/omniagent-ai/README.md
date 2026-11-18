
# ✦ OmniAgent AI

### Multimodal & Multilingual Agentic AI Powered by Gemini, Bodhan AI & MCP

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-blue?logo=python" />
  <img src="https://img.shields.io/badge/Streamlit-Live_App-FF4B4B?logo=streamlit" />
  <img src="https://img.shields.io/badge/LLM-Gemini_2.5_Flash-4285F4" />
  <img src="https://img.shields.io/badge/Protocol-MCP-8B5CF6" />
  <img src="https://img.shields.io/badge/Status-Deployed-brightgreen" />
</p>

<p align="center">
  <a href="https://omniagentai.streamlit.app/">
    <img src="https://img.shields.io/badge/🚀_Launch_Live_App-Streamlit-FF4B4B?style=for-the-badge" />
  </a>
  <a href="https://github.com/samirmore7/OmniAgentAI">
    <img src="https://img.shields.io/badge/💻_Source_Code-GitHub-181717?style=for-the-badge" />
  </a>
</p>

---

## 🌐 Live Application

**🚀 [Try OmniAgent AI](https://omniagentai.streamlit.app/)**

**💻 [GitHub Repository](https://github.com/samirmore7/OmniAgentAI)**

OmniAgent AI is a multimodal, multilingual AI agent that combines the reasoning capabilities of **Gemini 2.5 Flash** with specialized Indian-language AI services provided by **Bodhan AI**.

The system understands natural-language instructions, plans multi-step workflows, selects appropriate tools, and executes tasks through the **Model Context Protocol (MCP)**.

Users can interact with the agent through a Streamlit web application or Python CLI.

> **Deployment status:** The Streamlit application has a live deployment URL. End-to-end hosted workflow verification is still pending.

---

## 📌 Problem Statement

Indian-language processing often requires multiple specialized AI services.

For example, extracting Hindi text from an image and translating it into English traditionally involves:

1. Uploading the image to an OCR service.
2. Copying the extracted text.
3. Passing the text to a translation service.
4. Collecting and formatting the results.

This process becomes increasingly complex when combining OCR, translation, speech recognition, and text-to-speech.

**OmniAgent AI solves this problem by orchestrating multiple specialized tools through a single natural-language interface.**

Instead of manually choosing and connecting tools, users describe what they want, and the agent determines how to accomplish it.

---

## ✨ Key Features

### 🧠 Intelligent Agent Planning

- Uses Gemini 2.5 Flash as the reasoning engine.
- Understands natural-language instructions.
- Selects appropriate MCP tools.
- Supports multi-step task execution.
- Uses tool responses to determine subsequent actions.

### 🔎 Multilingual OCR

- Extracts text from uploaded images.
- Supports PNG, JPG, and JPEG uploads.
- Uses Bodhan AI's OCR capabilities.
- Returns extracted text as structured output.

### 🌐 Language Translation

- Translates text between supported languages.
- Supports translation after OCR.
- Preserves multilingual Unicode output.
- Uses Bodhan AI translation services.

### 🎙️ Speech Transcription

- Converts supported speech audio into text.
- Supports WAV uploads through the web interface.
- Includes audio-processing and chunking capabilities.
- Uses Bodhan AI speech recognition.

### 🔊 Text-to-Speech

- Integrates Bodhan AI speech generation.
- Converts supported text into spoken audio.
- Supports speech-generation workflows through MCP.

### 🔗 Multi-Step Agentic Workflows

OmniAgent can chain specialized tools automatically.

**Example:**

```text
User Instruction
      |
      v
Extract Hindi text from image
      |
      v
Bodhan OCR
      |
      v
Hindi Text
      |
      v
Bodhan Translation
      |
      v
English Translation
      |
      v
Final Result
```

### 🖥️ Interactive Streamlit Dashboard

- Modern responsive interface.
- Four visual themes.
- Seven interface language options.
- File upload support.
- Natural-language task input.
- Quick-start workflow examples.
- Configurable execution limits.
- Step-by-step tool output.
- Downloadable execution reports.

### 🛡️ Execution Controls

- Maximum planning steps.
- Maximum tool-call attempts.
- Retry handling.
- Tool execution tracking.
- Budget consumption monitoring.
- Cost visibility where pricing information is available.

---

## 🛠️ Technology Stack

| Component | Technology |
|---|---|
| Programming Language | Python 3.11 |
| AI Planner | Gemini 2.5 Flash |
| LLM API | OpenAI-compatible Gemini endpoint |
| Multilingual AI | Bodhan AI |
| Agent Architecture | Tool-calling agent |
| Tool Communication | Model Context Protocol (MCP) |
| MCP Transport | Local stdio |
| Web Framework | Streamlit |
| HTTP Client | HTTPX |
| Data Validation | Pydantic |
| Configuration | python-dotenv |
| Testing | Python regression tests |
| Reports | JSON, Markdown, JSONL |
| Deployment | Streamlit Community Cloud |
| Version Control | Git & GitHub |

---

## 🏗️ System Architecture

```text
                    +--------------------+
                    |        USER        |
                    +----------+---------+
                               |
                    +----------v---------+
                    |   Streamlit / CLI  |
                    +----------+---------+
                               |
                    +----------v---------+
                    |    OmniAgent AI    |
                    | Agent Orchestrator |
                    +----------+---------+
                               |
                    +----------v---------+
                    | Gemini 2.5 Flash   |
                    |  Planning Engine   |
                    +----------+---------+
                               |
                    +----------v---------+
                    | Tool Selection &   |
                    | Budget Validation  |
                    +----------+---------+
                               |
                    +----------v---------+
                    |     MCP Client     |
                    +----------+---------+
                               |
                    +----------v---------+
                    |  Local MCP Server  |
                    +----------+---------+
                               |
           +-------------------+-------------------+
           |                   |                   |
     +-----v------+      +-----v------+      +-----v------+
     | OCR Tool   |      | Translation|      | Audio Tools|
     +-----+------+      +-----+------+      +-----+------+
           |                   |                   |
           +-------------------+-------------------+
                               |
                    +----------v---------+
                    |     Bodhan AI      |
                    |   External APIs    |
                    +----------+---------+
                               |
                    +----------v---------+
                    |   Tool Responses   |
                    +----------+---------+
                               |
                    +----------v---------+
                    | Agent Continuation |
                    +----------+---------+
                               |
                    +----------v---------+
                    |  Final Results &   |
                    | Execution Reports  |
                    +--------------------+
```

### Architecture Explanation

**1. User Interface**

Users submit natural-language instructions through Streamlit or the command-line interface.

**2. Agent Orchestrator**

OmniAgent manages the overall workflow, including tool execution and result processing.

**3. Gemini Planner**

Gemini determines which tools are required to complete the requested task.

**4. MCP Client**

The agent communicates with a local MCP server using the Model Context Protocol.

**5. Bodhan MCP Server**

The server exposes specialized multilingual tools to the agent.

**6. Bodhan AI APIs**

The corresponding Bodhan services perform OCR, translation, transcription, and speech generation.

**7. Reporting Layer**

The system records tool results, execution details, budget usage, and structured reports.

---

## 🧰 Available AI Tools

| Tool | Description |
|---|---|
| `extract_text` | Extract text from an image |
| `translate_text` | Translate text between supported languages |
| `transcribe_audio` | Convert speech audio into text |
| `generate_speech` | Generate speech from text |

The tools are exposed through MCP and selected by the agent based on the user's instructions.

Actual language and voice support depends on the configured Bodhan AI services.

---

## 🖥️ Streamlit Web Application

**Live App:** https://omniagentai.streamlit.app/

The Streamlit application provides a user-friendly interface for interacting with OmniAgent AI.

### Dashboard Features

**1. AI Workspace**

Users can enter natural-language instructions describing their desired tasks.

**2. File Upload**

Supported file types:

```text
.png
.jpg
.jpeg
.wav
```

The interface allows up to three files per execution, with a maximum size of 15 MB per file.

**3. Quick-Start Examples**

- Extract text from an image.
- Extract and translate text into English.
- Transcribe speech from an uploaded WAV file.

**4. Execution Settings**

Users can configure:

- Maximum planning steps.
- Maximum tool calls.

**5. Results Dashboard**

The dashboard displays:

- Workflow status.
- Completed steps.
- Tool-call usage.
- Execution duration.
- Final agent output.
- Individual tool responses.
- Execution activity.

**6. Reports and Downloads**

Users can download execution results and available reports.

### Supported Interface Themes

- Midnight
- Aurora
- Royal
- Light

### Interface Languages

- English
- Hindi
- Marathi
- Tamil
- Telugu
- Bengali
- Kannada

Some interface labels may fall back to English.

---

## 📂 Project Structure

```text
OmniAgentAI/
│
├── src/
│   │
│   ├── bodhan_agent/
│   │   ├── agent.py
│   │   ├── cli.py
│   │   └── ... other agent modules
│   │
│   └── bodhan_mcp/
│       ├── server.py
│       ├── audio_tools.py
│       ├── ocr_tools.py
│       ├── translation_tools.py
│       └── tts_tools.py
│
├── tests/
│
├── demo_inputs/
│   └── sample_ocr.png
│
├── demo_outputs/
│   ├── report.json
│   ├── result.md
│   └── trace.jsonl
│
├── streamlit_app.py
├── requirements.txt
├── pyproject.toml
├── run_tests.py
├── .env.example
├── .gitignore
└── README.md
```

---

## ⚙️ Installation and Setup

### Prerequisites

Ensure you have:

- Python 3.11
- Git
- Gemini API credentials
- Bodhan AI API credentials
- Internet access

### Step 1 — Clone the Repository

```bash
git clone https://github.com/samirmore7/OmniAgentAI.git

cd OmniAgentAI
```

### Step 2 — Create a Virtual Environment

```bash
python -m venv .venv
```

Activate it.

**Windows PowerShell:**

```powershell
.\.venv\Scripts\Activate.ps1
```

**Linux/macOS:**

```bash
source .venv/bin/activate
```

### Step 3 — Install Dependencies

```bash
python -m pip install -r requirements.txt
```

### Step 4 — Configure API Credentials

Create a `.env` file in the project root.

```dotenv
# Gemini Configuration

LLM_API_KEY=your_gemini_api_key
LLM_BASE_URL=your_existing_llm_base_url
LLM_MODEL=gemini-2.5-flash

# Bodhan AI Configuration

BODHAN_BASE_URL=your_bodhan_api_base_url

BODHAN_OCR_KEY=your_ocr_api_key
BODHAN_TRANSLATE_KEY=your_translation_api_key
BODHAN_TRANSCRIBE_KEY=your_transcription_api_key
BODHAN_SPEAK_KEY=your_speech_api_key
```

Use the exact endpoint values provided by your API services.

**Important:** Never commit your `.env` file or actual API keys to GitHub.

### Step 5 — Launch Streamlit

```bash
python -m streamlit run streamlit_app.py
```

Open:

```text
http://localhost:8501
```

You can now access the OmniAgent AI dashboard locally.

---

## 🚀 Cloud Deployment

OmniAgent AI has been deployed to Streamlit Community Cloud.

### Live URL

**https://omniagentai.streamlit.app/**

### Deployment Configuration

| Setting | Value |
|---|---|
| Hosting | Streamlit Community Cloud |
| GitHub Repository | `samirmore7/OmniAgentAI` |
| Branch | `main` |
| Entry Point | `streamlit_app.py` |
| Dependencies | `requirements.txt` |
| Credentials | Streamlit Secrets |

### Streamlit Secrets

Configure the following environment variables in Streamlit Cloud's Secrets settings.

```toml
LLM_API_KEY = "YOUR_GEMINI_API_KEY"
LLM_BASE_URL = "YOUR_LLM_BASE_URL"
LLM_MODEL = "gemini-2.5-flash"

BODHAN_BASE_URL = "YOUR_BODHAN_BASE_URL"

BODHAN_OCR_KEY = "YOUR_OCR_KEY"
BODHAN_TRANSLATE_KEY = "YOUR_TRANSLATE_KEY"
BODHAN_TRANSCRIBE_KEY = "YOUR_TRANSCRIBE_KEY"
BODHAN_SPEAK_KEY = "YOUR_SPEAK_KEY"
```

Do not place real credentials in your repository.

The application uses a local MCP subprocess. End-to-end cloud execution depends on successful subprocess startup, Linux compatibility, and valid API credentials.

---

## 🧪 Demonstration — OCR + Translation

### User Instruction

> Extract Hindi text from sample_ocr.png and translate it into English.

### Step 1 — OCR

**Tool:** `extract_text`

**Input:** `sample_ocr.png`

**Language:** Hindi

**Extracted Text:**

```text
नमस्ते भारत
```

### Step 2 — Translation

**Tool:** `translate_text`

**Source Language:** Hindi

**Target Language:** English

**Translated Text:**

```text
hello india
```

### Final Result

```text
hello india
```

### Execution Report

```json
{
  "status": "success",
  "executed_tools": [
    "extract_text",
    "translate_text"
  ],
  "completed_steps": 2,
  "budget": {
    "max_tool_calls": 2,
    "tool_calls_used": 2,
    "remaining": 0,
    "budget_exhausted": true
  }
}
```

This workflow was successfully demonstrated using the local backend.

---

## 📊 Execution Budget Management

OmniAgent AI supports tool-call budgeting to control execution.

For example:

```text
Maximum Tool Calls: 2
```

The agent can perform up to two tool-call attempts within the configured budget.

### Example

| Metric | Value |
|---|---:|
| Maximum Tool Calls | 2 |
| Used Tool Calls | 2 |
| Remaining Calls | 0 |
| Completed Steps | 2 |
| Execution Status | Success |

The budget prevents additional tool calls once the configured limit is reached.

**Note:** Tool-call budgets do not represent monetary spending limits.

---

## 💰 Cost Tracking

OmniAgent records tool usage and available cost information.

Tracked fields include:

- Total tool calls.
- Tool calls grouped by service.
- Known costs in INR.
- Calls with unknown pricing.
- Cost completeness.

When pricing information is unavailable, OmniAgent marks the corresponding costs as unknown instead of assuming they are free.

This improves transparency in AI workflow execution.

---

## 📄 Reporting and Tracing

OmniAgent generates structured execution artifacts.

### 1. JSON Report

`report.json`

Contains:

- Original task.
- Execution status.
- Executed tools.
- Completed steps.
- Tool arguments and responses.
- Budget consumption.
- Cost metadata.

### 2. Markdown Summary

`result.md`

Provides a human-readable overview of the execution.

### 3. JSONL Trace

`trace.jsonl`

Records execution events for debugging, auditing, and workflow analysis.

Reports may include user-submitted content and should be reviewed before sharing.

---

## 🧪 Testing and Validation

The project includes offline regression tests for core agent functionality.

Run:

```bash
python run_tests.py
```

### Recorded Test Results

| Test Suite | Passed |
|---|---:|
| Audio Processing | 5 |
| Chunked Transcription | 4 |
| Transcription MCP | 9 |
| Agent Chunk Routing | 5 |
| Planner Retry | 5 |
| **Total** | **28** |

**28/28 offline checks passed** in the recorded local test run.

### Additional Verified Workflows

- Hindi image OCR.
- Hindi-to-English translation.
- OCR-to-translation tool chaining.
- Hindi WAV transcription.
- Structured report generation.
- Tool-call budget enforcement.

These results demonstrate local backend functionality. The hosted Streamlit deployment should be tested separately.

---

## 🔐 Security and Reliability

OmniAgent AI incorporates several development safeguards:

- Environment-based API configuration.
- No hardcoded API credentials required.
- Tool-call budgeting.
- Retry handling.
- File upload restrictions in Streamlit.
- Structured error reporting.
- Execution traces for debugging.

### Security Recommendations

For public deployments:

1. Keep API credentials in Streamlit Secrets.
2. Never expose private keys in GitHub.
3. Avoid uploading sensitive documents.
4. Monitor API quotas and usage.
5. Review logs before sharing.
6. Implement authentication and rate limiting for unrestricted public access.

---

## ⚠️ Known Limitations

- AI execution requires valid external API credentials.
- OCR quality depends on image clarity and supported languages.
- Speech transcription depends on audio quality.
- Translation accuracy varies by language and context.
- Gemini may occasionally select an unintended tool.
- Tool-call limits do not enforce financial spending limits.
- Some interface translations use English fallbacks.
- The Streamlit UI currently accepts WAV audio uploads.
- Public deployment requires ongoing API quota monitoring.
- Hosted MCP execution must be verified independently.

---

## 🔮 Future Enhancements

Potential future improvements include:

- PDF document processing.
- Additional audio formats.
- Multilingual conversational memory.
- Persistent workflow history.
- Enhanced agent observability.
- Authentication and user accounts.
- Per-user API usage limits.
- Improved cost estimation.
- Additional specialized MCP tools.
- Cloud-native MCP deployment.
- Workflow visualization.
- Batch document processing.

---

## 🎯 Project Outcomes

OmniAgent AI demonstrates practical experience in:

- Agentic AI architecture.
- LLM-based tool selection.
- Model Context Protocol integration.
- Multimodal processing.
- Multilingual AI services.
- API integration.
- Asynchronous Python.
- Workflow orchestration.
- Retry and budget management.
- Structured reporting.
- Streamlit application development.
- GitHub-based deployment.

The project illustrates how a general-purpose LLM can coordinate specialized AI services to complete real-world multilingual tasks.

---

## 👨‍💻 Developer

**Samir More**

B.Tech — Computer Science & Engineering  
Artificial Intelligence & Analytics

**GitHub:** https://github.com/samirmore7

**Portfolio:** https://samirmoreportfolio.netlify.app/

**Project Repository:** https://github.com/samirmore7/OmniAgentAI

**Live Application:** https://omniagentai.streamlit.app/

---

## ⭐ Support

If you find this project useful, consider starring the repository.

Contributions, suggestions, and feedback are welcome.

---

<p align="center">
  <strong>OmniAgent AI</strong>
</p>

<p align="center">
  Bridging intelligent agent orchestration and multilingual AI
  through Gemini, Bodhan AI, and MCP.
</p>

<p align="center">
  Built with Python • Powered by Gemini & Bodhan AI • Deployed on Streamlit
</p>
