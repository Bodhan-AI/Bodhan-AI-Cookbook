Title: `[Agentic Eng Assignment] Video dubbing — <your name>`
Branch: `assignment/video-dubbing-<github-handle>` · Folder: `examples/video_dubbing/`

**Type:** new recipe · **Path:** `examples/video_dubbing/` · **Bodhan models used:** indic-transcribe, indic-translate, indic-speak, indic-ocr (optional)

## Summary
Dubs an English video into any Indic-Speak language and writes an AI-dubbed MP4, SRT, per-sentence JSON, the English transcript and a machine-readable report. All transcription, translation and speech go to Bodhan models; an optional LLM only judges and edits English. A web studio lets a person review and fix any sentence.

## How to run
```bash
cd examples/video_dubbing
make setup && cp .env.example .env        # needs ffmpeg; fill in keys
python -m bodhan_dub --input path/to/video.mp4 --langs hi,ta,bn --out ./out
python -m bodhan_dub.web --port 8000      # optional web studio
```

## Architecture
See the diagram and one line per stage in the [README](../README.md#how-it-works). Components: `segmentation` (VAD, chunks, sentences), `bodhan` (client with retries, cache, cost meter), `llm` (English-only brain with guardrails), `timing` (fit ladder), `qa` (back-transcription checks), `audio` (mix, loudness, mux), `pipeline` (orchestration, per-language isolation), `web` (thin UI).

## Workflow
Flow chart with retries, checks and decision points: [docs/design_notes.md](design_notes.md#flow-chart-with-retries-checks-and-decisions).

## Where the LLM is used, and why
| Decision | Fallback |
|---|---|
| Group fragments into whole sentences | punctuation and pause heuristic |
| Build a glossary of names, acronyms, terms | none; user glossary still applies |
| Shorten English when the dub is too long | cap speed at 1.2x, mark degraded |
| Judge a borderline dub from its back-transcript | trust error-rate metrics |

English only; any output containing Indic script is rejected. LLM calls share a concurrency cap and retry on 429/5xx (Retry-After, else exponential backoff) before a decision falls back. A circuit breaker switches the LLM off for the run after 3 calls in a row fail after retries. **Honest note:** the cached source stage (grouping, glossary) was built while the LLM was unavailable (provider quota, then a retired model name), so it used the rule-based fallbacks; shortening and judging ran with the LLM in the final run (8 shortenings in Tamil, 6 in Bengali). A clean `--force` run would use the LLM for all four decisions. Recorded in `report.json` (`llm.*`). Model: `llama-3.3-70b-versatile (Groq)`.

## Bodhan models used
Indic-Transcribe (hosted: source English and back-transcription), Indic-Translate (hosted: all translation, `codemix` with `--code-mixed`), Indic-Speak (hosted: all speech, the language's own recorded voices, optional `style`), Indic-OCR (hosted, optional `--ocr`: on-screen text in sampled frames, translated and burned in). Chunks are kept to 25 s.

## Results
One real run on a 5.49-minute video, `--langs hi,ta,bn --ocr`. Outputs: https://github.com/anuragnayak01/Bodhan-AI-Cookbook/releases/tag/demo-v1

| | hi | ta | bn | Target |
|---|---|---|---|---|
| Lines dubbed / degraded | 25 / 0 | 24 / 1 | 23 / 2 | |
| Sync within ±300 ms | 100% | 100% | 100% | ±300 ms |
| Stretch range | 1.0–1.14 | 1.0–1.2 | 1.0–1.2 | 0.85–1.2 |
| Length difference | 0 ms | 0 ms | 0 ms | ≤100 ms |
| Speech LUFS | −16.08 | −16.09 | −16.30 | −16 |
| Mean / max CER | 5.6% / 29.7% | 3.5% / 12.4% | 5.7% / 23.2% | |
| LLM shortenings / speech retries | 0 / 1 | 8 / 0 | 6 / 7 | |
| On-screen text burned in | 7/7 | 7/7 | 7/7 | |

**Misses:** Tamil line 1 and Bengali lines 15 and 17 were still too long at 1.2× or had a high back-transcription error after shortening. They are marked `degraded` in the report, not hidden.

## Demo video
https://drive.google.com/file/d/13kSwj_-DzGF7YsehUKiEk9XBKb6f_Pv9/view

## Trade-offs and known limitations
See [README limitations](../README.md#limitations-stated-honestly). In short: a full run is slow because Bodhan rate-limits all three models (the client paces itself to the reported limits, so 3 languages of a 5.5-minute video took about 32 minutes); on some keys `/translate` returns 404, so the client falls back to the OpenAI-compatible route; a 429 caused by a spent budget is not retried; speaker split is a pitch heuristic; music under speech is lost; glossary enforcement is partial; QA thresholds need calibration on real output; on-screen text timing follows the frame-sampling interval.

## Cost and runtime
Measured on a 5.49-minute video, 3 languages (hi, ta, bn): the full run cost ₹6.72, about **₹0.41 per video minute per language**, in 32 minutes with rate-limit waits. The final cached re-run with LLM shortening added ₹1.44 and 4 minutes; including all re-runs, about ₹8.4 (about ₹0.5 per video minute per language). OCR price is unpublished and counted as 0.

## AI assistance
Claude Code (Claude Sonnet) was used for the pipeline, tests, web studio and docs; all code was reviewed and run by the author.


## Checklist (Cookbook)
- [x] Started from `examples/TEMPLATE/`; the folder matches the required layout
- [x] `README.md` explains what the recipe does, how to set it up (`## Setup`) and how to run it (`## Run`)
- [x] Dependencies are pinned (`name==version`) in `requirements.txt`
- [x] `.env.example` lists every variable; no real key anywhere in the diff
- [x] No notebooks, so no notebook outputs
- [x] Only current model ids from `scripts/bodhan_api_rules.json` are used
- [x] `make check` and `python scripts/validate_recipe.py examples/video_dubbing` pass locally
- [x] Ran the recipe end to end against `https://api.bodhan.ai` with my own keys
