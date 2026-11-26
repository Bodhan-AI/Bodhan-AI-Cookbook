# Architectural & Design Decisions

This document details key engineering tradeoffs and design choices in the pipeline.

## 1. Zero Modification of Video Presentation Timestamps
- **Context**: Dublaro included experimental video slowdown (`fit_video.py`) to fit slow speech.
- **Decision**: Video slowdown is explicitly forbidden. Video presentation timestamps are strictly preserved, maintaining exact original video duration within $\pm 100\text{ ms}$.
- **Rationale**: Changing video speed causes unnatural lip-sync, motion stuttering, and timeline discrepancies. Synchronization must be solved through audio time-stretching and semantic condensation.

## 2. English-Side Only Semantic Condensation
- **Context**: When spoken audio is overlong ($S > 1.20\times$), text must be shortened.
- **Decision**: The agent never edits or rewrites the translated Indian-language text directly. It modifies only the source English text, then feeds the revised text back into Bodhan Indic-Translate.
- **Rationale**: LLM rewriting of Indic text can introduce grammatical hallucinations, unnatural register shifts, and dialect errors. Bodhan Indic-Translate is the single authoritative translation provider.

## 3. Hermetic Mock Fallback vs. Live APIs
- **Context**: Live inference endpoints may experience rate limits (HTTP 429) or transient 500 errors.
- **Decision**: API clients feature automated exponential backoff with a compliant acoustic fallback generator. Automated tests run in hermetic mock mode, eliminating credit depletion during CI.
- **Rationale**: Production media pipelines must remain resilient to transient network partitions and upstream API maintenance.

## 4. Indic Unicode Normalization in CER Calculation
- **Context**: Indic scripts use combining vowel signs (matras), nuktas, and halants that can be represented via multiple Unicode codepoint sequences.
- **Decision**: All back-transcribed and reference text is normalized using Unicode NFC and stripped of punctuation before calculating Levenshtein distance.
- **Rationale**: Prevents artificial CER inflation caused by equivalent rendering forms.
