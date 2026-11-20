# Bodhan MCP Tool Server & Task Agent

An agentic AI system that connects an LLM-powered task agent to Bodhan AI language tools through the Model Context Protocol (MCP). The agent plans tasks, selects tools, executes workflows, verifies results, and manages execution limits and estimated costs.

**Bodhan models:** `indic-transcribe`, `indic-translate`, `indic-speak`, `indic-ocr`
**Languages demonstrated:** Hindi, Bengali, and multilingual document/audio workflows
**Format:** Python CLI application + MCP tool server

## What it does

This project implements an MCP server exposing four Bodhan AI capabilities and an LLM-driven task agent that can use these tools to complete language-related tasks.

Users can provide a task and input files. The agent determines the necessary steps, selects the appropriate tools, executes them, and saves outputs and execution traces for review.

The project includes planning, tool execution, argument building, verification, result formatting, budget tracking, and step-limit enforcement.

## Features

- MCP server exposing Bodhan AI tools
- LLM-based task planning
- Multi-step tool execution
- Audio transcription
- Text translation
- Text-to-speech generation
- Image and PDF OCR
- Tool result verification
- Execution step limits
- Estimated API cost budget enforcement
- Trace logging
- Markdown result report
- JSON execution report

## Bodhan tools

| Tool | Purpose | Input | Output |
|---|---|---|---|
| `indic_transcribe` | Speech-to-text | Audio file | Transcribed text |
| `indic_translate` | Text translation | Text + target language | Translated text |
| `indic_speak` | Text-to-speech | Text + language + voice | Audio file |
| `indic_ocr` | OCR | Image/PDF | Extracted text |

### Tool implementation details

- **Transcription:** Audio is chunked to keep individual requests within approximately 29 seconds.
- **Translation:** Long text is split into chunks of approximately 3,000 characters.
- **Speech generation:** Text is chunked into segments of up to approximately 250 characters.
- **OCR:** Supports image and PDF inputs.

## Setup

### Requirements

- Python 3.10+
- Bodhan AI API credentials
- LLM API credentials for the configured OpenAI-compatible endpoint
- Git

### 1. Clone the repository

```bash
git clone https://github.com/Bodhan-AI/Bodhan-AI-Cookbook.git
cd Bodhan-AI-Cookbook/examples/bodhan_tool_agent
```

### 2. Create a virtual environment

**Windows CMD:**

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

**Linux/macOS:**

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy the example environment file:

**Windows CMD:**

```cmd
copy .env.example .env
```

**Linux/macOS:**

```bash
cp .env.example .env
```

Fill in the required credentials in `.env`. Never commit real API keys.

## Run

The agent CLI accepts a natural-language task, input files, an output directory, maximum execution steps, and an estimated cost limit.

### CLI arguments

| Argument | Description |
|---|---|
| `--task` | Natural-language task description |
| `--files` | Input file path(s) |
| `--out` | Output directory |
| `--max-steps` | Maximum number of agent execution steps |
| `--max-cost-inr` | Maximum estimated budget in INR |

### Example

```bash
python -m bodhan_agent --task "Extract the text from this document and translate it into Bengali" --files sample_data/document.pdf --out outputs/document_translation --max-steps 5 --max-cost-inr 2
```

Run the CLI from the recipe directory with the `src` directory configured on Python's module path if required by your environment.

Generated output is saved in the selected output directory.

## How it works

The agent follows a structured execution pipeline:

1. **Task input** – receives the user's task and file paths.
2. **Planning** – the LLM creates a plan using available MCP tools.
3. **Argument building** – prepares tool arguments from the task context.
4. **Budget check** – verifies execution against the estimated cost budget.
5. **Tool execution** – invokes Bodhan tools through the MCP client.
6. **Verification** – checks the tool results and determines whether more steps are needed.
7. **Result formatting** – creates a human-readable final report.
8. **Trace logging** – records execution details for inspection.

## Architecture

The project is divided into two major parts:

### MCP tool server

Exposes Bodhan capabilities as MCP tools:

- Transcription
- Translation
- Text-to-speech
- OCR

### Task agent

The task agent is responsible for:

- Interpreting the user task
- Planning tool calls
- Building tool arguments
- Calling MCP tools
- Verifying tool results
- Tracking budget and execution limits
- Formatting the final result

## Output structure

A typical execution generates:

```text
outputs/
└── task_name/
    ├── result.md
    ├── trace.jsonl
    ├── report.json
    └── artifacts/
        └── generated_files
```

| File | Purpose |
|---|---|
| `result.md` | Human-readable task result |
| `trace.jsonl` | Step-by-step execution trace |
| `report.json` | Structured execution report |
| `artifacts/` | Generated files such as audio |

## Example tasks

### OCR

```text
Extract all text from the provided image.
```

### OCR + translation

```text
Extract text from the PDF and translate it into Bengali.
```

### Audio transcription

```text
Transcribe the supplied audio into text.
```

### Multistep task

```text
Extract text from this document, translate it into Hindi, and generate speech from the translated text.
```

The agent determines the required tools and executes the workflow within the configured limits.

## Limitations

- Results depend on the quality of the source image, PDF, or audio.
- OCR may produce character errors, particularly in complex layouts or regional scripts.
- Long audio and text inputs are handled through chunking, which may affect context across chunk boundaries.
- Tool/API availability and response times depend on the Bodhan services.
- Speech generation may fail if the upstream API is unavailable.
- The agent's planning and tool selection depend on the configured LLM.
- Cost enforcement uses estimates and is not a guarantee of the final provider bill.

## Budget and execution controls

The CLI provides:

- Maximum agent steps
- Estimated cost limit in INR
- Tool execution tracking
- Result verification
- Execution traces

The current implementation uses an estimated cost of ₹0.25 per tool call for budget tracking. This is a project-side estimate, not a quoted Bodhan API price.

## Testing

The project includes end-to-end checks for:

- OCR on images
- OCR followed by translation
- Audio transcription
- Multi-page PDF OCR
- Budget enforcement
- Maximum step enforcement
- TTS error handling
- TTS output-path safety

Tests involving external APIs require valid credentials and service availability.

## AI assistance

AI tools were used during development for assistance with code generation, debugging, documentation, and implementation guidance. The final implementation was tested and reviewed through local execution and end-to-end checks.

## Project status

This is an assignment project demonstrating an MCP-based tool server and an agentic task execution workflow using Bodhan AI capabilities.
