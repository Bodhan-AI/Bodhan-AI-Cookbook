# Security & Prompt Injection Defense

## Threat Model & Mitigations

1. **Prompt Injection Protection**:
   - Untrusted text extracted from OCR or transcription is treated strictly as data, never as system instructions.
   - Text is scanned for malicious prompt injection patterns (e.g. `ignore all previous instructions`, `developer mode`, `system:`) and defanged.
2. **Filesystem & Path Traversal Guard**:
   - Paths are strictly resolved using `resolve_safe_path()`.
   - Null bytes (`\0`), traversal symbols (`..`), and non-existent files are rejected with structured exceptions.
3. **File Size & Memory Protection**:
   - Files exceeding 100 MB are rejected to prevent memory exhaustion.
   - Empty files (0 bytes) are flagged immediately without wasting API calls.
4. **Credential Isolation**:
   - API keys are read exclusively from environment variables (`BODHAN_API_KEY`, `LLM_API_KEY`).
   - All authorization tokens, keys, and secrets are automatically redacted in `trace.jsonl` and console logs.
5. **No Shell Execution from Model Content**:
   - The agent contains zero dynamic shell execution (`eval`, `exec`, `os.system`) based on LLM or Bodhan model outputs.
