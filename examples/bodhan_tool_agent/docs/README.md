# Bodhan AI Project Documentation

This directory contains the documentation suite for the Bodhan Tool Server and Task Agent submission, matching the requirements of the [Bodhan AI Cookbook](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook) and the Take-Home Assignment.

---

## Documentation Index

| Document | Description | Format |
|---|---|:---:|
| [**Architecture Specification**](ARCHITECTURE.md) | High-level system architecture, boundaries between code and LLM, MCP server, and task agent components. | Markdown |
| [**Architecture Diagram**](architecture.svg) | Visual architectural diagram of FastMCP tools, task agent, and Indic models. | SVG Diagram |
| [**Agent Design**](AGENT_DESIGN.md) | Detailed agent flow, deterministic resolver waterfall, pre-flight budget calculation, executor, and verifiers. | Markdown |
| [**Agent Flow Diagram**](agent-flow.svg) | Flow chart of the agent loop (boot $\to$ inventory $\to$ resolve $\to$ pre-flight $\to$ plan $\to$ step loop $\to$ report). | SVG Diagram |
| [**Tool Design & Schemas**](TOOL_DESIGN.md) | Granularity choices, model limits absorbed (chunking, 32K pagination, PDF page loop), typed schemas, and error codes. | Markdown |
| [**Evaluation Report**](EVALUATION_REPORT.md) | Full live test report of 10 evaluation cases against live Bodhan Indic models and Groq LLM (10/10 passed). | Markdown |
| [**PR Description Template**](PR_DESCRIPTION.md) | Complete 12-section pull request description ready to paste into GitHub. | Markdown |
| [**Submission Checklist**](SUBMISSION_CHECKLIST.md) | Complete checklist of technical requirements, ground rules, and final submission steps. | Markdown |

---

## Core Technical Reference Files (in Project Root)

* [`../README.md`](../README.md): Main repository entrypoint with setup instructions, CLI commands, and test guide.
* [`../pyproject.toml`](../pyproject.toml): Pinned Python project configuration and package dependencies.
* [`../Dockerfile`](../Dockerfile): Production container specification for one-command reproducible execution.
* [`../.env.example`](../.env.example): Sanitized environment variables template.
* [`../SPEC.md`](../SPEC.md): Technical requirements and engineering constraints.
* [`../AGENTS.md`](../AGENTS.md): Operational guide and coding rules.
