# Live Bodhan API & Groq LLM Agent Evaluation Report

- **Execution Timestamp**: 2026-11-20 17:18:32
- **LLM Planner**: Groq API with Multi-Model Fallback (`gpt-oss-120b`, `qwen/qwen3.8-27b`, `gpt-oss-20b`)
- **Live Bodhan Models Evaluated**: `indic-transcribe`, `indic-translate`, `indic-speak`, `indic-ocr`
- **Total Cumulative Spend**: **₹0.0197** (Well within ₹2.00 ceiling!)
- **Evaluation Score**: **10 / 10 PASSED (100%)**

## 1. Executive Summary Table

| ID | Test Scenario | Live Model / Action | Status | Cost (INR) | Verdict |
|---|---|---|---|---|---|
| LIVE-01 | Live Audio Speech Transcription (indic-transcribe) | `indic-transcribe` | `success` | ₹0.0174 | **PASSED** |
| LIVE-02 | Live Speech Transcription + English Translation (indic-transcribe + indic-translate) | `indic-transcribe + indic-translate` | `success` | ₹0.0010 | **PASSED** |
| LIVE-03 | Live Indic-to-Indic Text Translation (Hindi -> Tamil) | `indic-translate` | `success` | ₹0.0006 | **PASSED** |
| LIVE-04 | Live English-to-Hindi Text Translation (indic-translate) | `indic-translate` | `success` | ₹0.0007 | **PASSED** |
| LIVE-05 | Pre-flight Budget Guard Test (Exhaustion Refusal) | `none (pre-flight stop)` | `budget_exhausted` | ₹0.0000 | **PASSED** |
| LIVE-06 | Missing File Guard Test (Constraint C8) | `none (resolver stop)` | `impossible` | ₹0.0000 | **PASSED** |
| LIVE-07 | Corrupt Media Error Handling (UNREADABLE_MEDIA) | `none (local reject)` | `success` | ₹0.0000 | **PASSED** |
| LIVE-08 | Unsupported Language Refusal (Constraint C8) | `none (pre-flight stop)` | `impossible` | ₹0.0000 | **PASSED** |
| LIVE-09 | Local Audio Chunking & Inspection (Free Tool Verification) | `local_tools (inspect_media, chunk_audio)` | `success` | ₹0.0000 | **PASSED** |
| LIVE-10 | Upstream 500 Handling & Graceful Degradation (Constraint C6) | `indic-ocr (live upstream probe)` | `success` | ₹0.0000 | **PASSED** |

## 2. Detailed Per-Case Trace & Audit Logs

### LIVE-01: Live Audio Speech Transcription (indic-transcribe)

- **Task**: "Transcribe speech_hindi.wav to Hindi text with timestamps."
- **Model Invoked**: `indic-transcribe`
- **Cost**: ₹0.0174
- **Status**: `success` | **Verdict**: PASSED
- **Groq Planner**: Chosen Model `openai/gpt-oss-120b` in `3132.03 ms`
- **Executed Tool Steps**:
  - `[s1] transcribe_audio`: hi transcript (10.435s, 0 segments): 'ही होप्ड देर वुड बी स्टेव फॉर डिनर टर्नि...' (Latency: 1436.61ms, Cost: ₹0.0174)

### LIVE-02: Live Speech Transcription + English Translation (indic-transcribe + indic-translate)

- **Task**: "Transcribe speech_hindi.wav and translate it to English."
- **Model Invoked**: `indic-transcribe + indic-translate`
- **Cost**: ₹0.0010
- **Status**: `success` | **Verdict**: PASSED
- **Groq Planner**: Chosen Model `openai/gpt-oss-120b` in `2383.79 ms`
- **Executed Tool Steps**:
  - `[s1] transcribe_audio`: hi transcript (10.435s, 0 segments): 'ही होप्ड देर वुड बी स्टेव फॉर डिनर टर्नि...' (Latency: 3.75ms, Cost: ₹0.0000)
  - `[s2] translate_text`: Translated 161 chars: 'This hoped-for would be stewed for dinne...' (Latency: 344.36ms, Cost: ₹0.0010)

### LIVE-03: Live Indic-to-Indic Text Translation (Hindi -> Tamil)

- **Task**: "Translate the public health notice in press_release_hindi.txt from Hindi to Tamil."
- **Model Invoked**: `indic-translate`
- **Cost**: ₹0.0006
- **Status**: `success` | **Verdict**: PASSED
- **Groq Planner**: Chosen Model `openai/gpt-oss-120b` in `2079.28 ms`
- **Executed Tool Steps**:
  - `[s1] translate_text`: Translated 118 chars: 'இந்திய அரசு இன்று புதிய டிஜிட்டல் கல்வி ...' (Latency: 270.35ms, Cost: ₹0.0006)

### LIVE-04: Live English-to-Hindi Text Translation (indic-translate)

- **Task**: "Translate the announcement in english_notice.txt to Hindi."
- **Model Invoked**: `indic-translate`
- **Cost**: ₹0.0007
- **Status**: `success` | **Verdict**: PASSED
- **Groq Planner**: Chosen Model `openai/gpt-oss-120b` in `2642.33 ms`
- **Executed Tool Steps**:
  - `[s1] translate_text`: Translated 93 chars: 'भारत में डिजिटल पहलों के आधिकारिक पोर्टल...' (Latency: 289.28ms, Cost: ₹0.0007)

### LIVE-05: Pre-flight Budget Guard Test (Exhaustion Refusal)

- **Task**: "Transcribe speech_hindi.wav to Hindi text."
- **Model Invoked**: `none (pre-flight stop)`
- **Cost**: ₹0.0000
- **Status**: `budget_exhausted` | **Verdict**: PASSED

### LIVE-06: Missing File Guard Test (Constraint C8)

- **Task**: "Transcribe non_existent_recording_2026.wav to Hindi text."
- **Model Invoked**: `none (resolver stop)`
- **Cost**: ₹0.0000
- **Status**: `impossible` | **Verdict**: PASSED

### LIVE-07: Corrupt Media Error Handling (UNREADABLE_MEDIA)

- **Task**: "Transcribe corrupt_sample.wav to Hindi text."
- **Model Invoked**: `none (local reject)`
- **Cost**: ₹0.0000
- **Status**: `success` | **Verdict**: PASSED
- **Groq Planner**: Chosen Model `openai/gpt-oss-120b` in `2491.5 ms`
- **Executed Tool Steps**:
  - `[s1] transcribe_audio`: Error: UNREADABLE_MEDIA - Failed to read audio file: Unreadable audio media at D:\bodhi_ai\demo\inputs\test_suite_10\case_01_audio_transcribe\corrupt_sample.wav: Error opening 'D:\\bodhi_ai\\demo\\inputs\\test_suite_10\\case_01_audio_transcribe\\corrupt_sample.wav': Format not recognised. (Latency: 1.44ms, Cost: ₹0.0000)

### LIVE-08: Unsupported Language Refusal (Constraint C8)

- **Task**: "Translate press_release_hindi.txt into German."
- **Model Invoked**: `none (pre-flight stop)`
- **Cost**: ₹0.0000
- **Status**: `impossible` | **Verdict**: PASSED

### LIVE-09: Local Audio Chunking & Inspection (Free Tool Verification)

- **Task**: "Inspect lecture_long_45s.wav and chunk the audio into slices under 30 seconds."
- **Model Invoked**: `local_tools (inspect_media, chunk_audio)`
- **Cost**: ₹0.0000
- **Status**: `success` | **Verdict**: PASSED
- **Groq Planner**: Chosen Model `openai/gpt-oss-120b` in `1915.29 ms`
- **Executed Tool Steps**:
  - `[s1] inspect_media`: audio media (wav, 1.373MB) (Latency: 1.32ms, Cost: ₹0.0000)
  - `[s2] chunk_audio`: 2 audio chunks created (Latency: 27.19ms, Cost: ₹0.0000)

### LIVE-10: Upstream 500 Handling & Graceful Degradation (Constraint C6)

- **Task**: "Perform OCR on legal_circular.pdf to extract all text."
- **Model Invoked**: `indic-ocr (live upstream probe)`
- **Cost**: ₹0.0000
- **Status**: `success` | **Verdict**: PASSED
- **Groq Planner**: Chosen Model `openai/gpt-oss-120b` in `1853.54 ms`
- **Executed Tool Steps**:
  - `[s1] ocr_document`: Error: INTERNAL - OCR failed on image: Server error '500 Internal Server Error' for url 'https://api.bodhan.ai/v1/ocr'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500 (Latency: 18287.91ms, Cost: ₹0.0000)

