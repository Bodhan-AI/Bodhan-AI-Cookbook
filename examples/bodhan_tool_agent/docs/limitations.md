# Known Trade-offs & Limitations

1. **Audio Chunking Boundaries**:
   - For audio longer than 28 seconds, chunking is performed using duration-based or silence-aware cuts. In clips with continuous speech without pauses, word splitting at boundary edges is minimized using standard window overlap.
2. **Translation Language Pair Support**:
   - Translation is exclusively supported between English and the 22 scheduled Indian languages. Non-Indic targets (e.g., French, German, Spanish) are not supported by Bodhan and are rejected with explicit, honest error diagnostics.
3. **PDF Rasterization**:
   - Scanned PDF processing requires rasterization to images before OCR. Rasterization is performed at 150 DPI using `pypdfium2` for optimal balance of upload bandwidth and OCR character fidelity.
4. **Companion LLM Policy**:
   - The companion LLM is used strictly for high-level reasoning and English summarization. All Indian language translation, OCR, transcription, and speech synthesis are strictly executed via Bodhan AI models.
