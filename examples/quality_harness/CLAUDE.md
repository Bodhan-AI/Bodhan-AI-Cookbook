# Project rules: bodhan_eval (Bodhan quality harness)

## Hard rules. Breaking any of these fails the submission
1. Every translation, speech synthesis and speech recognition call goes to a Bodhan model
   (Indic-Translate, Indic-Speak, Indic-Transcribe) via https://api.bodhan.ai/v1. No Whisper, no Google/Azure/AWS
   speech or translate, no IndicTrans2, no other TTS/ASR/MT, even as a fallback.
2. No learned quality-estimation models (COMET, BLEURT, BERTScore, LaBSE similarity, sentence-transformers, etc.).
   String metrics are fine (sacrebleu chrF++/BLEU, edit distance, difflib, rapidfuzz).
3. The general LLM may READ Indian-language text and judge it. It must NEVER write, rewrite, translate or "correct"
   Indian-language text. When the LLM quotes a span, the harness must verify it is a verbatim substring of the input
   and discard it otherwise. The harness evaluates; it never fixes.
4. The error injector creates Indian-language errors only programmatically (delete spans, change digits, swap terms,
   break markup, cut/duplicate/silence audio) or by perturbing the ENGLISH source and re-translating with Indic-Translate.
5. No thresholds, regexes or rules special-cased to my demo dataset. Defaults must be language-agnostic; per-language
   values come from calibration and live in a config file, never in `if lang == "hi"` branches.
6. Never commit keys, tokens or generated audio. Keep `.env.example` only. Audio goes in .gitignore.
7. Config via env only: BODHAN_TRANSLATE_KEY, BODHAN_SPEAK_KEY, BODHAN_TRANSCRIBE_KEY, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY.
   LLM client = OpenAI-compatible only (the `openai` package with base_url). No provider-only features without a fallback.

## Engineering rules
- Python 3.11+, package `bodhan_eval` under src/, pinned dependencies, ruff + pytest.
- Everything async with a global concurrency limit; retries with exponential backoff + jitter on 429/5xx/timeouts.
- Every model output is cached on disk keyed by sha256 of (model, endpoint, canonical params, exact input bytes).
  A rerun from cache is byte-identical and costs 0.
- A bad or partial item never stops the run: log it, skip it, count it in the summary with a reason.
- Every flag carries evidence: which check, the numbers, the span, and the raw artifacts needed to trace it.
- Checks are plugins (one file each, registered by decorator) so a new check can be added in minutes.
- Unknown languages must work: language list lives in config/languages.yaml, scripts are detected by Unicode block.
- Bodhan API endpoint paths and parameter names live in config/bodhan_api.yaml (OpenAI-compatible by default),
  so they can be corrected against the real docs without code changes.
- Small commits per phase. Run `pytest` and `ruff` before each commit.
