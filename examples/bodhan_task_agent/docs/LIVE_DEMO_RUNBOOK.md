# Live demonstration and PR runbook

This is the six-task demonstration plan and remaining work. Some tasks already ran; see `LIVE_VALIDATION_RESULTS.md` for exact status and avoid counting independent tool tests as completed autonomous tasks. All demos must make genuine Bodhan API calls and retain unedited outputs and traces. Bodhan will execute hidden tasks, so do not add special-case routing for these examples.

## Prepare once

1. Fork [Bodhan AI Cookbook](https://github.com/Bodhan-AI/Bodhan-AI-Cookbook), copy this recipe into `examples/bodhan_task_agent/`, and use branch `assignment/tool-agent-KowshiqKatta`.
2. Install Python and ffmpeg; `python -m pip install -e '.[dev]'` in the recipe directory.
3. Configure `.env` *privately* using `BODHAN_*_KEY` model keys and an `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` that support standard tool calling. Never commit `.env`.
4. Create a `demo_inputs/` folder outside git. Use files you own, including one small scanned chapter, one multilingual worksheet image, one Hindi WAV, five short speech clips and a text paragraph. Links to clearly licensed samples are preferable in README.

## Six real demonstrations

The goal is to show *different tool combinations* through the same task agent. These are examples, not hard-coded tests. Observed status on 22 November: Bengali speech completed; Hindi-to-English SRT completed; worksheet OCR/direct Marathi translation observed but the agent's autonomous Marathi run failed; the unsupported-French text refusal passed, but the assignment's exact MP4 variant was not tested; Tamil chapter narration and Telugu audio-clip detection remain untested.

| Run | CLI task | Input | Verify |
|---|---|---|---|
| 1 | `Make a two-minute Tamil audio summary of this chapter` | scanned PDF | actual OCR, translated Tamil Markdown, speech WAV/MP3, back-ASR QA |
| 2 | `Transcribe this Hindi voice note and give me English subtitles` | Hindi WAV | transcript segments, English SRT, time windows stated accurately |
| 3 | `Translate this worksheet into Marathi and preserve the table` | worksheet photo | OCR Markdown, preserved table, numbers/LaTeX QA |
| 4 | `Which of these audio clips are in Telugu? Translate those into English` | five audio clips | correct language indications, no invented classification for ambiguous script |
| 5 | `Read this paragraph aloud in Bengali with a female voice` | text file | correct fixed library voice, playable audio, back-ASR QA |
| 6 | `Translate this video into French` | MP4 | explain unsupported target honestly; no fake French output |

Command template (from the recipe root):

```bash
python -m bodhan_agent --task "<TASK FROM TABLE>" --files demo_inputs/<TASK_FOLDER> --out outputs/demo_01 --max-steps 30 --max-cost-inr 20
```

Check each `outputs/demo_XX/report.json`: status, actual artifacts, estimated cost, QA fields, and `trace.jsonl`. The unsupported case should be explicitly marked not completed, with a clear reason. A clear failure is the right answer for an impossible request.

## Independent MCP client

Use an off-the-shelf MCP client such as [MCP Inspector](https://github.com/modelcontextprotocol/inspector). Test `tools/list`, one tool successfully, one malformed argument and one budget refusal. A Python-only protocol unit test is not a substitute for a true independent client check.

```bash
npx @modelcontextprotocol/inspector --cli python -m bodhan_mcp --method tools/list
```

## Clean-clone checks before PR

From the Cookbook repository root:

```bash
python scripts/validate_recipe.py examples/bodhan_task_agent
make check
```

Check `.env` is ignored, none of the `outputs` or media files are tracked, and README links are publicly accessible. The Cookbook itself requires a real model-API run.

## Demo recording (≤5 minutes)

Show the repository, architecture diagram, terminal agent/server commands, real generated Tamil/Bengali audio and worksheet/SRT outputs, the impossible-language response, JSONL trace/QA and cost report. Use a recorder of your choice; store the video somewhere reviewers can open **without signing in**. Link it in the README and PR.

## Pull request and final email

- Branch: `assignment/tool-agent-KowshiqKatta`
- PR title requested by hiring task: `[Agentic Eng Assignment] Tool server and agent — Kowshiq Kattamuri`
- Open from your fork into `Bodhan-AI/Bodhan-AI-Cookbook:main`.
- Include filled-in `PR_BODY.md` with links to **genuine** demo files and real QA observations; remove placeholders before submission.
- Email reply to original Barry thread with **PR URL and final commit SHA** by the deadline. The exact SHA is what they evaluate; later commits may not count.

Deadline from 16 November 2026 email: **23 November 2026**. Confirm any cutoff time directly with Bodhan because the email specifies one week but not a precise time.
