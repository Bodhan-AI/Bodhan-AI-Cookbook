# Task List - Bodhan Tool Server & Task Agent
**Last Updated:** 2026-11-22  
**Status Legend:** ⬜ TODO | 🔄 IN PROGRESS | ✅ DONE | ❌ BLOCKED | ⏭ SKIPPED (stretch)

---

## Phase 0: Project Bootstrap
| # | Task | Status | Notes |
|---|---|---|---|
| 0.1 | Create folder structure (`src/bodhan_mcp`, `src/bodhan_agent`, `tests`, `docs`) | ✅ DONE | Complete package structure |
| 0.2 | Write PRD.md | ✅ DONE | Requirements and boundaries |
| 0.3 | Write ARCHITECTURE.md | ✅ DONE | Full design and data flow |
| 0.4 | Write TASK_LIST.md (this file) | ✅ DONE | Tracked throughout |
| 0.5 | Write DECISIONS.md | ✅ DONE | ADR records |
| 0.6 | Write RESUME_BRIEFING.md | ✅ DONE | Context preservation |
| 0.7 | Create `pyproject.toml` with pinned deps | ✅ DONE | Editable package setup |
| 0.8 | Create `.env.example` | ✅ DONE | No secrets committed |
| 0.9 | Create `Makefile` (setup, check, test targets) | ✅ DONE | Standard commands |
| 0.10 | Create `Dockerfile` | ✅ DONE | Containerized build |

---

## Phase 1: Bodhan API Client Layer
| # | Task | Status | Notes |
|---|---|---|---|
| 1.1 | Write `bodhan_client.py` - shared OpenAI-compat client | ✅ DONE | All 4 models supported |
| 1.2 | Add retry logic (exponential backoff, 5 retries, 429 Retry-After) | ✅ DONE | Resilient backoff |
| 1.3 | Add concurrency limiter (asyncio Semaphore, limit=2) | ✅ DONE | Prevents rate bursting |
| 1.4 | Add per-call cost tracking (return cost from every call) | ✅ DONE | Accurate metering |
| 1.5 | Test client against live Bodhan API | ✅ DONE | Verified with live endpoints |

---

## Phase 2: MCP Tool Implementations
| # | Task | Status | Notes |
|---|---|---|---|
| 2.1 | `transcribe.py` - Indic-Transcribe wrapper | ✅ DONE | ≤28s chunking, auto-detect & segment stitching |
| 2.2 | `translate.py` - Indic-Translate wrapper | ✅ DONE | Paragraph chunking, markdown preservation |
| 2.3 | `speak.py` - Indic-Speak wrapper | ✅ DONE | Sentence chunking, PCM16 WAV stitching |
| 2.4 | `ocr.py` - Indic-OCR wrapper | ✅ DONE | pypdfium2 PDF rasterizer (no poppler C-dep) |
| 2.5 | `language_detect.py` - Unicode script heuristic | ✅ DONE | 23 Indic scripts detected instantly |
| 2.6 | `audio_chunk.py` - VAD-aware splitter | ✅ DONE | Silence + hard split fallback |
| 2.7 | `media_info.py` - Audio, PDF, image inspector | ✅ DONE | Format, page count, duration |
| 2.8 | `summarize.py` - English LLM summarizer | ✅ DONE | Calibrated speaking duration (~150 wpm) |
| 2.9 | `subtitles.py` - SRT subtitle builder | ✅ DONE | Millisecond alignment, robust coercion |
| 2.10 | Unit test: `test_audio_chunk.py` | ✅ DONE | 4/4 passed |
| 2.11 | Unit test: `test_translate.py` | ✅ DONE | 5/5 passed |

---

## Phase 3: MCP Server
| # | Task | Status | Notes |
|---|---|---|---|
| 3.1 | Set up FastMCP server in `server.py` | ✅ DONE | FastMCP protocol |
| 3.2 | Register all 9 tools with typed schemas + descriptions | ✅ DONE | Full type hints and annotations |
| 3.3 | Structured error handling | ✅ DONE | Clean dict errors, no crashes |
| 3.4 | `__main__.py` - stdio MCP server entry point | ✅ DONE | `python -m bodhan_mcp` |
| 3.5 | Validate tool discovery and schema reflection | ✅ DONE | Tested over stdio and FastMCP introspection |

---

## Phase 4: Agent Core
| # | Task | Status | Notes |
|---|---|---|---|
| 4.1 | `cache.py` - SQLite run-scoped result cache | ✅ DONE | Zero-cost replay |
| 4.2 | `budget.py` - BudgetManager | ✅ DONE | Pre-flight assertions, step & cost limit |
| 4.3 | `trace.py` - trace.jsonl + report.json writers | ✅ DONE | Audit logs for every step |
| 4.4 | `sanitizer.py` - prompt injection guard | ✅ DONE | Demarcation tags + SYSTEM_GUARD |
| 4.5 | `planner.py` - LLM-based task planner | ✅ DONE | JSON plan with reference resolution |
| 4.6 | `verifier.py` - output verification | ✅ DONE | Back-transcribe Levenshtein + LLM back-translation |
| 4.7 | `executor.py` - tool call dispatcher | ✅ DONE | Object reference resolution `{s2.field}` |
| 4.8 | `agent.py` - main agent loop | ✅ DONE | plan ➔ execute ➔ verify ➔ trace |
| 4.9 | `__main__.py` - CLI entry point | ✅ DONE | `python -m bodhan_agent` |
| 4.10 | Unit test: `test_budget.py` | ✅ DONE | 6/6 passed |
| 4.11 | Unit test: `test_cache.py` | ✅ DONE | 5/5 passed |
| 4.12 | Unit test: `test_sanitizer.py` | ✅ DONE | 5/5 passed |

---

## Phase 5: End-to-End Demo Tasks
| # | Task | Status | Output |
|---|---|---|---|
| 5.1 | Tamil audio summary of scanned PDF | ✅ DONE | `speech_f3511697.wav` (6.8 MB), `summary_en.txt`, `translated_ta.txt` |
| 5.2 | Hindi voice note → English SRT subtitles | ✅ DONE | `english_subtitles.srt`, `translated_en.txt` |
| 5.3 | Worksheet photo → Marathi Markdown with table | ✅ DONE | `translated_mr.txt` (Marathi table preserved) |
| 5.4 | Identify Telugu clips + translate to English | ✅ DONE | `translated_en.txt` (Telugu identified & translated) |
| 5.5 | Read paragraph aloud in Bengali (female voice) | ✅ DONE | `speech_032f8fb1.wav` (Ishita female voice) |
| 5.6 | Translate MP4 to French (impossible task) | ✅ DONE | Honest refusal, `result.md` |

---

## Phase 6: Integration & Verification
| # | Task | Status | Notes |
|---|---|---|---|
| 6.1 | Full demo suite execution (`run_demo_tasks.py`) | ✅ DONE | All 6 tasks completed in 205s (₹3.44 total) |
| 6.2 | Offline unit test suite (`pytest tests/`) | ✅ DONE | 25/25 tests passing in 9.5s |
| 6.3 | Schema and secret hygiene check | ✅ DONE | `.gitignore` verified, zero keys tracked |

---

## Phase 7: Documentation & PR
| # | Task | Status | Notes |
|---|---|---|---|
| 7.1 | Write comprehensive `README.md` | ✅ DONE | Mermaid diagrams, results table, architecture |
| 7.2 | Add Mermaid architecture diagram to README | ✅ DONE | In README and ARCHITECTURE.md |
| 7.3 | Add agent flow chart to README | ✅ DONE | In README and ARCHITECTURE.md |
| 7.4 | Document tool design, verification, budgets | ✅ DONE | In README and DECISIONS.md |
| 7.5 | Record demo video ≤ 5 minutes | 🔄 READY FOR USER | User can record screen or walkthrough |
| 7.6 | Push recipe to `Bodhan-AI-Cookbook` fork | 🔄 READY | User fork and branch setup |
| 7.7 | Open PR with description template | 🔄 READY | PR description template prepared |
| 7.8 | Reply on email thread with PR link + commit SHA | 🔄 READY | Email template prepared |
