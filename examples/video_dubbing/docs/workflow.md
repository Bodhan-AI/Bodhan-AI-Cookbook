# End-to-End Pipeline Workflow

This document details the lifecycle of a dubbing request through the Bodhan.AI pipeline.

## 1. Video Ingestion & Validation
- **Supported Formats**: MP4, MOV, MKV, WebM.
- **Duration**: 1–15 minutes (warnings emitted if outside bounds).
- **Checks**:
  - Video stream existence and valid frame rate.
  - Audio stream existence, sample rate, and channels.
  - Absence of video stream corruption.

## 2. Audio Extraction
Two representations are extracted:
1. **Speech Track**: 16 kHz, 16-bit PCM, Mono WAV (ideal for ASR).
2. **HQ Audio Track**: 48 kHz, 16-bit PCM, Stereo WAV (preserved for background ambience mixing).

## 3. English Speech Recognition
- Bodhan Indic-Transcribe extracts spoken English sentences and timestamps.
- Long audio segments (>25s) are windowed to respect recommended ASR limits.
- The transcript is written to `out/transcript.en.json`.

## 4. Segment-Wise Translation
- Bodhan Indic-Translate translates English segments into requested target languages (e.g., Hindi `hi`, Tamil `ta`, Bengali `bn`).
- Optional glossary mappings ensure strict technical term consistency.
- Translation responses are cached in memory to eliminate redundant requests.

## 5. Speech Synthesis
- Bodhan Indic-Speak synthesizes regional audio for each translated segment.
- Language-specific voice profiles (e.g., `Ananya`) are selected from config.

## 6. Agentic Synchronization & Condensation
- Available window duration: $T_{\text{target}} = \text{end} - \text{start}$.
- Spoken audio duration: $T_{\text{audio}}$.
- Required speedup: $S = T_{\text{audio}} / T_{\text{target}}$.
- **Case 1: $0.85 \le S \le 1.20$**:
  Audio is fitted using FFmpeg pitch-preserving `atempo` filters.
- **Case 2: $S > 1.20$ (Speech Overlong)**:
  - Agent evaluates English source text.
  - `EnglishCondenser` reduces syllable count while preserving semantics.
  - Revised English is re-sent to Bodhan Indic-Translate.
  - Speech is re-synthesized via Bodhan Indic-Speak.
  - If still exceeding after bounded retries (default 2), flagged as `MANUAL_REVIEW`.

## 7. Quality Verification (QA Loop)
- Generated audio is back-transcribed using Bodhan Indic-Transcribe.
- Indic Unicode normalization (NFC, character sets) is applied.
- Character Error Rate (CER) and Levenshtein distance are measured.
- Audio artifact inspection detects clipping and silence.

## 8. Mastering & Multiplexing
- Fitted audio segments are placed onto a continuous PCM timeline.
- Master timeline duration is matched to video duration within $\pm 100\text{ ms}$.
- Background audio is ducked (-12 dB) and mixed with dubbed speech.
- EBU R128 loudness normalization is applied (-16 LUFS $\pm 1$).
- FFmpeg multiplexes audio and video with soft subtitles.
- Master `report.json`, SRT, and VTT files are emitted.
