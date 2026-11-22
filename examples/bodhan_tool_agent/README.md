# Bodhan Tool Server and Task Agent

This project does two things:

1. **A tool server (MCP server).** It gives any AI app four abilities from Bodhan's Indian-language models: turn speech
   into text, translate, read text out of images and scanned pages (OCR), and turn text into speech. It handles each
   model's limits itself, so the caller never has to cut audio or split long documents.
2. **A task agent.** You give it a task in plain words and some files, for example *"Make a one-minute Tamil audio
   summary of this chapter"*. It makes a plan, uses the tools, checks its own work, and writes the answer to an output
   folder. It stays inside a budget (tool calls, rupees, seconds) and says clearly when a task cannot be done.

In plain words: the **LLM** is the chat model that decides what to do next; the **MCP server** is a small program that
other AI apps can plug into to use the tools; **OCR** means reading text from pictures.

Rules this project follows:

* Every speech-to-text, translation, text-to-speech and OCR step is done by a Bodhan model. Nothing else is used.
* The LLM only plans and writes **English**. It is not allowed to write, fix or read out Indian-language text itself:
  the code rejects it. Indian-language text only comes from the Bodhan tools.
* The agent never makes up output. If a task cannot be done, it says so and produces no files.

> **Tested on the real services.** `scripts/live_smoke.py` calls each of the four Bodhan models once (costs under ₹1) and
> passed with real keys. Seven end-to-end tasks were then run with a real LLM (see [Results](#results)). 79 automatic
> tests also pass offline. Tried on Windows 10/11 (PowerShell, Python 3.10) and Linux (Python 3.12).

## How to run

You need Python 3.10 or newer and **ffmpeg** (`winget install Gyan.FFmpeg` on Windows, `brew install ffmpeg` on Mac,
`sudo apt install ffmpeg` on Ubuntu).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate        Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e . --no-deps
```

Copy `.env.example` to `.env` and fill in the four Bodhan keys and your LLM details (`LLM_BASE_URL`, `LLM_MODEL`,
`LLM_API_KEY`; any OpenAI-compatible service that supports tool calling works). Never commit `.env`.

```bash
python scripts/live_smoke.py       # optional: checks your four Bodhan keys (under ₹1)
python -m pytest -q                # 79 tests, offline and free
```

**Run the agent:**

```bash
python -m bodhan_agent \
  --task "Make a two-minute Tamil audio summary of this chapter" \
  --files path/to/inputs/ --out ./out [--max-steps 30] [--max-cost-inr 20]
```

Other options: `--max-seconds 600`, `--call-timeout 180`, and `--use-mcp` (make the agent reach the tools through the MCP
server instead of calling the same code directly). It writes:

```
out/
  result.md      what was done, the answer, assumptions, links to the files
  artifacts/     audio, subtitles, Markdown, ...
  trace.jsonl    one line per step: plan, tool, arguments, result, time, cost
  report.json    status, steps, checks, total cost and time
```

**Run the MCP server on its own:**

```bash
python -m bodhan_mcp
```

It is a normal local MCP server, so any MCP client can use it. `tests/test_server.py` drives it with the official **MCP
Python SDK client**: it lists the tools and their typed outputs, sends bad inputs and checks the error replies, reads the
voice-list resource, and runs with a missing key. Other clients (Claude Desktop, Cursor, MCP Inspector) use the same
kind of config; I have not tried those:

```json
{"mcpServers": {"bodhan": {"command": "/path/to/.venv/bin/python", "args": ["-m", "bodhan_mcp"],
  "env": {"BODHAN_TRANSCRIBE_KEY": "...", "BODHAN_TRANSLATE_KEY": "...", "BODHAN_SPEAK_KEY": "...",
          "BODHAN_OCR_KEY": "...", "BODHAN_WORK_DIR": "/tmp/bodhan_work"}}}}
```

**Demo inputs and demo tasks:** `python scripts/make_demo_inputs.py` creates safe test inputs (a generated 3-page chapter
PDF, a worksheet image, a text file, a 3-second test video, and five voice clips made with Indic-Speak itself). Then run
`bash scripts/run_demo_tasks.sh` (Linux/Mac) or `.\scripts\run_demo_tasks.ps1` (Windows). To see which models your LLM
provider offers: `python scripts/list_llm_models.py`. A `Dockerfile` is included; I have not built it.

## Architecture

![Architecture diagram: the task agent talks to an LLM and to the MCP server; the server uses shared helpers and the Bodhan API](docs/architecture.png)

| Part | What it does |
| --- | --- |
| **Task agent** (`src/bodhan_agent`) | Runs the loop: plan, call tools, check results. Enforces the budget, keeps the safety rules, runs the final checks and writes the output folder. |
| **LLM** | Any OpenAI-compatible service. Makes the plan, chooses tools, writes English text, decides what to do when a check fails. |
| **MCP server** (`src/bodhan_mcp`) | The 12 tools. Every call returns a typed result with its cost and time, or a clear error with a code and a hint. It never crashes on bad input. |
| **Shared helpers** | One HTTP client (retries, limit on parallel calls, disk cache, rupee counting, budget check) and ffmpeg / a PDF reader for cutting audio and turning pages into images. |
| **Bodhan API** | The four hosted models. |
| **Output folder** | `result.md`, `artifacts/`, `trace.jsonl`, `report.json`. |

## Agent flow

![Flow chart of the agent loop: look at files, plan, check budget, choose and run tools, read results, finish, check the work, write outputs](docs/agent-flow.png)

In short: look at the input files, ask the LLM for a short plan, then repeat *choose tools → run them → read the
results* until the LLM says it is finished or a budget runs out. Before a run can end, the agent checks every generated
audio file and every translation. If a check fails, the LLM is told and gets up to two chances to fix it; after that the
run ends as `partial` and says what failed. If three tool calls in a row fail, the LLM is asked to try another approach.

## Tool design

**Why these tools.** One tool for every raw API call would force the LLM to remember each model's limits, and it would
forget them. One giant tool such as `dub_video` would hide too much and break on anything unexpected. So each tool does
one clear job on a **whole file**, and the code inside handles the limits. The LLM decides *what* to do and in what
order; the tools know *how*.

| Tool | What it does | Cost | Limit it handles for you |
| --- | --- | --- | --- |
| `inspect_file` | Describes a file or folder: type, length, pages, size | free | Look before spending money |
| `list_capabilities` | Languages, the 45 voices, limits, prices | free | Lets the agent check feasibility (French is not supported) |
| `detect_text_language` | Guesses the language of text from its script | free | Says "not sure" when a script is shared by several languages |
| `detect_audio_language` | Guesses the language of speech from the first seconds | about ₹0.05 | Same, for audio |
| `transcribe_audio` | Speech to text for audio or video of any length, with timestamps | ₹0.10 per minute | Models take at most 30 s: cuts at pauses, skips long silences, never sends silence |
| `translate_segments`, `write_subtitles` | Translate timed text, write SRT or VTT subtitles | tiny | Keeps timings; splits long lines into readable subtitles |
| `translate_text` | Translate text or a Markdown file | ₹0.20 per 10,000 output tokens | Long text is split at paragraphs; tables, LaTeX and code are never split; cut-off answers are retried in smaller pieces |
| `verify_translation` | Checks a translation without a reference | free | Right script? numbers kept? same headings, table rows and formulas? text dropped? optional round trip to English |
| `ocr_document` | Reads a PDF or image into Markdown | ₹0.20 per page | PDFs become page images; a bad page does not lose the others; headers and page numbers dropped; tables kept; doubtful pages listed |
| `synthesize_speech` | Text to an MP3 or WAV, tagged "AI-generated" | ₹6 per 10,000 characters | Sends sentence-sized pieces; redoes a piece that sounds cut or looped; evens out loudness |
| `verify_speech` | Listens back to generated audio and compares it with the text | ₹0.10 per minute | Reports errors, repeated phrases, missing sentences |

All tools take media and long text **as file paths**, never as raw data in the chat, and only a short preview comes back.
The agent also has three small helpers of its own: read a text file, write an English text file, and `finish`.
Files can only be written inside the output folder.

## Where the LLM is used, and why

| What the LLM decides | If it fails or gets it wrong |
| --- | --- |
| Reads the task and the file list, writes a short plan, says if the task looks impossible | The plan is advice only; the loop works without it |
| Which tool to call next and with what arguments (several at once when they are independent) | Wrong arguments come back as clear errors it can fix; three failures in a row make it change approach |
| Writes English summaries (saved with `write_text_file`, English only) | Non-English text is rejected by the code |
| What to do when a check fails | After two rounds the agent ends the run as `partial` by itself |
| When to call `finish`, and what to say | If it stops without `finish`, it is nudged twice and then recorded as `partial` |

**Done by plain code, not by the LLM:** looking at the files first, guessing language from script, splitting audio and
text, retries, budgets and cost, the cache, every quality check, the rule that Indian-language text cannot come from the
LLM, and the final status. If the LLM says "completed" but a check still fails, the status is changed to `partial`.

## Budgets, safety and checks

* **Budgets.** `--max-steps`, `--max-cost-inr`, `--max-seconds`, `--call-timeout`. The cost of a call is checked and held
  *before* the call, so even parallel calls cannot go over the limit. When a budget runs out the agent stops, writes what
  it has, and says what is missing.
* **When something goes wrong.** Failed requests are retried with waiting time in between. Cut-off translations are
  split and retried. A speech piece that sounds wrong is redone. One bad OCR page does not lose the rest.
* **Checks before finishing.** Generated audio is listened back to with Indic-Transcribe and compared with the text.
  Translations are checked for the right script, kept numbers, and unchanged headings, table rows and formulas. Short
  English sources also get a round trip back to English.
* **Untrusted text.** Text from files, scans and speech is treated as data. Previews are marked as untrusted, the LLM is
  told never to follow instructions found in them, and the tools cannot do anything harmful anyway (no shell, no web
  access, files only inside the output folder).
* **No repeated paid calls.** Results are cached on disk by exact input, and identical calls inside one run are reused.

## Bodhan models used

Hosted API at `https://api.bodhan.ai`:

* **Indic-Transcribe:** 16 kHz mono WAV pieces under 28 seconds; the language is passed whenever it is known.
* **Indic-Translate:** `POST /translate` first. With my keys that address answered "404 Not Found", so the code
  automatically switches to the other documented route (`/v1/chat/completions`, one user message
  `Translate the following text into <language>:`), which gives the same output. `BODHAN_TRANSLATE_TRANSPORT` can force
  one route.
* **Indic-Speak:** 24 kHz WAV; the voice is chosen from the library by language and gender.
* **Indic-OCR:** JPEG page images; tables as Markdown by default (HTML for merged cells).

## Results

Run with the `openai/gpt-oss-120b` model on Groq's free plan, and the hosted Bodhan API. Each run folder has
`result.md`, `trace.jsonl`, `report.json` and `artifacts/` (media files are not committed).

| # | Task | Status | Cost | Time | Tool calls | Details |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | One-minute Tamil audio summary of a 3-page scanned chapter | completed | ₹0.94 | 170 s | 7 | [result](runs/01/result.md) · [trace](runs/01/trace.jsonl) |
| 2 | Hindi voice note to English subtitles (SRT) | completed | ₹0.03 (first run) | 166 s | 6 | [result](runs/02/result.md) · [SRT rerun](runs/02b/result.md) |
| 3 | Worksheet photo to Marathi Markdown, table kept | completed | ₹0.20 | 146 s | 5 | [result](runs/03/result.md) · [trace](runs/03/trace.jsonl) |
| 4 | Which of four clips are Telugu, translated to English | completed | ₹0.03 | 322 s | 14 | [result](runs/04/result.md) · [trace](runs/04/trace.jsonl) |
| 5 | Bengali audio, female voice, from an English paragraph | completed | ₹0.09 | 70 s | 4 | [result](runs/05/result.md) · [trace](runs/05/trace.jsonl) |
| 6 | Translate a video into French (**impossible**) | impossible, no files | ₹0.00 | 3 s | 1 | [result](runs/06/result.md) · [trace](runs/06/trace.jsonl) |
| 7 | Task 1 with a ₹0.30 budget (**too small**) | partial, stopped by the budget | ₹0.20 | 102 s | 5 | [result](runs/07/result.md) · [trace](runs/07/trace.jsonl) |

Notes:

* **Task 1 is smaller than the assignment's example** (a generated 3-page chapter and a one-minute summary, instead of
  8 pages and two minutes) to stay inside the ₹10 trial credit. One OCR page was already cached from task 7, so a run
  from scratch costs about ₹1.1. Speech output is the expensive part.
* **Task 2** first produced a WebVTT file. I then changed the instructions to prefer SRT; `runs/02b` is the SRT rerun
  (the Bodhan results came from the cache, so it cost ₹0.00).
* **Task 3:** the heading, the sentence and all four table rows came back in Marathi with the table intact and the
  numbers 90, 180 and 360 unchanged. The English text read from the image is kept as an extra file.
* **Task 6** ends with a plain explanation that no Bodhan model supports French, and no media file is made.
* **Task 7** stopped before speech output: that step would have cost about ₹0.40 against the ₹0.30 limit, so it was
  refused before any money was spent. Total spend ₹0.20, always below the limit. The result is marked `partial`.
* The final checks run automatically before a run can end; their results are in `report.json` under `verification`.

How the runs compare with the assignment's targets:

| Target | What happened |
| --- | --- |
| Tasks completed | All 7 runs ended in the intended state (5 completed, 1 impossible, 1 partial by budget). I did not have the hidden test set. |
| Honest failure | Tasks 6 and 7 ended with an explanation and no made-up output |
| Everything checked | Every generated audio file and translation is checked before the run can end |
| No repeated paid calls | Disk cache and reuse; `report.json` counts repeated requests (target is 0) |
| Budget | Never went over `--max-cost-inr`; the cost is held before each call, so parallel calls cannot overspend (task 7) |
| Speed | **Missed with the free LLM.** Bodhan answers in 1-2 seconds, but runs took 70-320 seconds because the free LLM plan makes the agent wait (see below). Task 4 took more than 5 minutes. |
| Tool usability | Descriptions, typed results and error messages are exercised through the official MCP client in `tests/test_server.py` |

## Demo video

**Link:** https://drive.google.com/file/d/1iGrEDfby4DCIf6VMgx9zcil0-PsjR6hq/view?usp=sharing

## Trade-offs and known limitations

* **The free LLM makes runs slow.** The long gaps in the traces come from Groq's free plan, which limits how much text it
  handles per minute, so the agent has to wait and try again. Bodhan itself answers in 1-2 seconds. With a faster LLM the
  same runs would be much shorter. This is why the speed target was missed.
* **Bodhan's `/translate` address did not work with my keys** ("404 Not Found"). The code switches by itself to the other
  documented route, which gives the same output.
* **The LLM sometimes sends a badly formed request to a tool**, and Groq refuses it. The agent asks again (up to four
  times). This happened once in task 3 and the run still finished.
* **Subtitle timing is approximate.** A short clip is transcribed as one piece, so the subtitle changes inside it are
  spread by text length, not matched to the exact words. The lowercase style of the transcript comes from the model.
* **Names and terms may be translated differently in different parts of a long document.** The translation model has
  no glossary option.
* **Language is guessed from the script.** Some languages share a script (Hindi, Marathi and Nepali; Bengali and
  Assamese; Urdu, Kashmiri and Sindhi). The agent makes its best guess and writes down the assumption.
* **The quality checks are simple automatic checks, not a human review.** The limits I used (for example how different the
  spoken audio may be from the text) are reasonable guesses that I did not tune on real data. A failed check means "take
  a look", not proof. No native speaker reviewed the Indian-language outputs.
* **Not supported:** dubbing a video, separating speakers, mixing audio. Such tasks end as `impossible` or `partial` with
  an explanation.
* **Very long audio is handled in one go**, not as a live stream.
* **The offline tests use a pretend Bodhan service.** They prove the plumbing, the limits and the recovery, not the
  quality of translations or speech. Quality was only checked in the seven real runs above.
* **Windows PowerShell may show Indian-language text as strange characters.** The files are fine; open them in an editor
  such as VS Code.
* **Not built (optional extras):** a web page that shows the plan live, resuming a stopped run, a login-protected HTTP
  version of the server, and a script that scores its own test set.

## Cost and runtime

Prices: speech to text ₹0.10 per minute, translation ₹0.20 per 10,000 output tokens, text to speech ₹6 per 10,000
characters, OCR ₹0.20 per image. The cost of each demo task is in the [Results](#results) table. The seven runs
together cost about ₹1.5 of the ₹10 trial credit (plus a few paise for the smoke test and for making the demo clips).
Each run reports its cost in `report.json` (`total_cost_inr` and `cost_by_tool_inr`).

## AI assistance

I built this with **Claude** (in a chat window, not an IDE agent). I used it to discuss the design and to write the code
for the MCP server, the agent loop, the checks and the offline tests. I ran everything against the real Bodhan API and a
real LLM myself, read through the code, and fixed what the real runs showed: the `/translate` 404, the Groq rate limits
and badly formed tool requests, Windows text-encoding problems, and the budget overspend with parallel calls that I found
while running task 7.

## Project layout

```
src/bodhan_mcp/      the MCP server and its tools, Bodhan client, checks
src/bodhan_agent/    the task agent: loop, budgets, LLM client, prompts
tests/               79 offline tests (pretend Bodhan service, scripted LLM, real MCP client)
scripts/             live_smoke.py, make_demo_inputs.py, run_demo_tasks.sh / .ps1, list_llm_models.py
docs/                architecture.png, agent-flow.png, make_diagrams.py (redraws both), PR_DESCRIPTION.md
runs/                results of the seven demo tasks (add after running them)
```
