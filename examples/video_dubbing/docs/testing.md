# Testing Strategy & Acceptance Verification

The test suite covers unit tests, hermetic integration tests, and strict acceptance criteria tests.

## Running Tests

Run the full suite using pytest:
```bash
python -m pytest tests/
```

Run specific test tiers:
```bash
# Unit tests
python -m pytest tests/unit/

# Hermetic integration tests
python -m pytest tests/integration/

# Acceptance criteria tests
python -m pytest tests/acceptance/
```

---

## Test Hierarchy

### 1. Unit Tests (`tests/unit/`)
- `test_video_engine.py`: Tests container format support (MP4, MOV, MKV, WebM) and rejection of invalid formats.
- `test_audio_sync.py`: Verifies cascading FFmpeg `atempo` filter construction and master PCM timeline duration arithmetic.
- `test_condenser.py`: Verifies filler phrase pruning, verb simplification, and syllable reduction on the English source text.
- `test_qa_verifier.py`: Tests Indic Unicode NFC normalization, Levenshtein distance calculations, and Character Error Rate (CER) bounds.
- `test_subtitles.py`: Verifies SRT (`HH:MM:SS,mmm`) and WebVTT (`HH:MM:SS.mmm`) timecode formatting and text encoding.

### 2. Integration Tests (`tests/integration/`)
- `test_clients.py`: Verifies isolated client adapter interfaces, parameter mappings, and fallback behaviors.
- `test_pipeline_mock.py`: End-to-end execution of the pipeline using a synthetic test video, verifying output directory hierarchy and report structure.

### 3. Acceptance Tests (`tests/acceptance/`)
- `test_acceptance_criteria.py`: Programmatically tests and verifies:
  1. Timeline preservation within $\pm 100\text{ ms}$.
  2. Segment alignment within $\pm 300\text{ ms}$.
  3. Audio speedup factors restricted to $0.85\times \le S \le 1.20\times$.
  4. Generation of valid `subtitles.srt`, `subtitles.vtt`, `segments.json`, and `report.json`.
  5. Multi-language output generation for Hindi (`hi`), Tamil (`ta`), and Bengali (`bn`).
