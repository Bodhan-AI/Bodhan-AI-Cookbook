# Checkpoint and local runtime reference

Run these commands from the recipe root. Use the [main README](../../README.md) for a full run.

## Offline language checkpoint

```bash
python -m bodhan_lesson --input inputs/Lines-and-Angles-English-scanned.pdf \
  --langs hi --out .local/lines-english-override --source-lang en --until language
python -m json.tool .local/lines-english-override/source/language.json

python -m bodhan_lesson --input inputs/Lines-and-Angles-Hindi-scanned.pdf \
  --langs ta --out .local/lines-hindi-override --source-lang hi --until language
python -m json.tool .local/lines-hindi-override/source/language.json
```

The override writes `en` for the English scan and `hi` for the Hindi scan, with
method `override` and empty samples and attempts.
Use `--until language` to stop after language selection. Code-mixed narration is
unavailable at this checkpoint.

## Resume a run

Reuse the same `--input` and `--out` and advance `--until`. For example, a
completed `scripts` checkpoint can continue straight to `speech`.
The runner keeps checkpoint state under `<out>/work/checkpoints/` and checks
the ordered input files and settings before reusing it. In a terminal it asks
`Resume [Y/n]?` when saved work matches. Press Enter or type `y` to resume.
Type `n` to clear generated output and start over. Noninteractive runs resume
automatically. Assigning a new voice rebuilds scripts and speech while keeping
the checked translations.

The CLI prints when stages start, finish or reuse a checkpoint. It also prints
hosted rate-limit retry delays. Completed OCR pages, translation responses,
synthesis candidates and transcriptions are saved during their stages. After
an interruption, reuse the same command to pick up the unfinished calls.

Older output directories with compatible narration scripts can also resume at
speech when the saved page and attempt records match. For a fresh run, add
`--force`. It clears generated artifacts and checkpoint state under `--out`
before running the requested stages. Use it to retry a completed checkpoint
that recorded provider failures.

## Offline page normalization

With the recipe environment active, run the checkpoint for each supplied scan.
The source overrides keep both commands offline.

```bash
python -m bodhan_lesson --input inputs/Lines-and-Angles-English-scanned.pdf \
  --langs hi --out .local/lines-english-preprocess --source-lang en --until preprocess
python -m json.tool .local/lines-english-preprocess/work/images/p001.transform.json

python -m bodhan_lesson --input inputs/Lines-and-Angles-Hindi-scanned.pdf \
  --langs ta --out .local/lines-hindi-preprocess --source-lang hi --until preprocess
python -m json.tool .local/lines-hindi-preprocess/work/images/p001.transform.json
```

Compare each `work/images/p001.png` with `work/originals/p001.png` in the same
output directory. The input and output SHA-256 values hash those exact PNG files.
`original_size` and `normalized_size` are width and height in pixels. The
two-row `normalized_to_original` matrix maps normalized pixel edges back to the
original raster. Boxes are mapped at all four corners, rounded outward, and
clipped to the original bounds. An unreadable page gets a failed transform record
with a reason, and later pages continue.

Normalization applies EXIF orientation first. Automatic quarter-turn correction
requires consistent Latin baseline evidence on both halves of a landscape
English page. Small deskew requires agreement between both page halves. Page
margins are preserved.

## Hosted automatic detection

Set `BODHAN_OCR_KEY` and use `BODHAN_BACKEND=hosted`. Latin and shared Indic
scripts require a label from a general-purpose model. Set `LLM_BASE_URL`,
`LLM_MODEL`, and `LLM_API_KEY` for an OpenAI-compatible Chat Completions endpoint.
The CLI reads `.env` from the current directory. Exported shell variables take
precedence over values in the file.
See the [.env.example](../../.env.example) variable names and the [Bodhan OCR API reference](https://console.bodhan.ai/api-docs/#document-ocr-api).

```bash
BODHAN_BACKEND=hosted python -m bodhan_lesson \
  --input inputs/Lines-and-Angles-English-scanned.pdf \
  --langs hi --out .local/lines-english --until language
python -m json.tool .local/lines-english/source/language.json

BODHAN_BACKEND=hosted python -m bodhan_lesson \
  --input inputs/Lines-and-Angles-Hindi-scanned.pdf \
  --langs ta --out .local/lines-hindi --until language
python -m json.tool .local/lines-hindi/source/language.json
```

Expect `en` and `hi` respectively, with method `llm`. If OCR evidence is missing
or the classifier cannot resolve it, the checkpoint writes a null code with a
reason. It records per-page sample outcomes and model attempts, with zero hosted
charge for local inference and an estimated cost for hosted OCR.

Place local scans under `inputs/` and choose a separate output folder per chapter.
The assignment's English demo is the [Lines and Angles chapter](https://www.aspirationsinstitute.com/wp-content/uploads/2021/01/9-Maths-NCERT-Book-Lines-and-Angles.pdf).
The working checkout may also contain the provided Hindi scan.

## Optional local GPU runtime

The default `local-first` mode uses Bodhan's open Indic-OCR weights. Install the
GPU runtime separately with the official installer. It requires a compatible NVIDIA
driver and `uv`. Accept access terms for the model weights on Hugging Face and
authenticate there if prompted. [Bodhan installer](https://github.com/Bodhan-AI/bodhan_genai#install) · [Indic-OCR weights](https://huggingface.co/bodhan-ai/indic-ocr)

From the recipe root, use the vendor environment for local inference.

```bash
mkdir -p .local
git clone https://github.com/Bodhan-AI/bodhan_genai.git .local/bodhan_genai
(cd .local/bodhan_genai && ./install.sh --extras all-ocr)
source .local/bodhan_genai/.venv/bin/activate
uv pip install -e .
BODHAN_BACKEND=local-first python -m bodhan_lesson \
  --input inputs/Lines-and-Angles-English-scanned.pdf \
  --langs hi --out .local/local-ocr --until language
python -m json.tool .local/local-ocr/source/language.json
```

Weights load on the first OCR request. A confirmed CUDA out-of-memory exception
triggers hosted fallback for that request and needs `BODHAN_OCR_KEY`. Later
requests start locally again. Other local errors appear in the attempt record.
Set the same `LLM_*` variables for ambiguous source languages in either mode.

For the full local pipeline, install the remaining inference extras into the
same vendor environment.

```bash
uv pip install --python .local/bodhan_genai/.venv/bin/python \
  -e '.local/bodhan_genai[mt-infer,tts-infer,asr-infer]' -e .
```

The local adapters load their official weights on demand. Accept model access
terms and authenticate on Hugging Face where prompted. [Indic-OCR](https://huggingface.co/bodhan-ai/indic-ocr) · [Indic-Translate](https://huggingface.co/bodhan-ai/indic-translate) · [Indic-Speak](https://huggingface.co/bodhan-ai/indic-speak) · [Indic-Transcribe Core](https://huggingface.co/bodhan-ai/indic-transcribe-core)

If vLLM fails to import TorchCodec with `libnvrtc.so.13` missing and your
PyTorch build is `+cu129`, install the CUDA 12.9 TorchCodec wheel in Bodhan's
environment. [PyTorch CUDA 12.9 wheel index](https://download.pytorch.org/whl/cu129/torchcodec/)

```bash
uv pip install --python .local/bodhan_genai/.venv/bin/python --no-deps \
  --index-url https://download.pytorch.org/whl/cu129 'torchcodec==0.16.0+cu129'
```

## Extract structured OCR

Use the hosted Indic-OCR key in `BODHAN_OCR_KEY` for these commands. The source
overrides skip language classifier calls. Hosted OCR runs for every normalized
page and may retry transient failures.

```bash
BODHAN_BACKEND=hosted python -m bodhan_lesson \
  --input inputs/Lines-and-Angles-English-scanned.pdf \
  --langs hi --out .local/lines-english-ocr --source-lang en --until ocr
python -m json.tool .local/lines-english-ocr/source/pages/p001.json

BODHAN_BACKEND=hosted python -m bodhan_lesson \
  --input inputs/Lines-and-Angles-Hindi-scanned.pdf \
  --langs ta --out .local/lines-hindi-ocr --source-lang hi --until ocr
python -m json.tool .local/lines-hindi-ocr/source/pages/p001.json
```

Each page record has a sanitized provider response, normalized image digest,
attempt history and typed blocks. `raw_block_ids` keeps array order and
`ordered_block_ids` uses unique provider ranks when available. Block IDs are
assigned in raw order. Boxes use normalized image pixels, and confidence measures
layout detection. Captions link to an adjacent figure or table when that
association is unambiguous. Malformed structures keep their source text with a
flag. An unreadable image or unusable response gets a failed page record.
[Bodhan OCR response schema](https://console.bodhan.ai/api-docs/#document-ocr-api)

## Resolve page layout

Run the same chapter command with `--until layout` and a dedicated output
directory. Use the hosted Indic-OCR key and configure `LLM_*` if you want
ambiguous pages to use the layout judge.

```bash
BODHAN_BACKEND=hosted python -m bodhan_lesson \
  --input inputs/Lines-and-Angles-English-scanned.pdf \
  --langs hi --out .local/lines-english-layout --source-lang en --until layout
python -m json.tool --no-ensure-ascii .local/lines-english-layout/work/layout.json
```

`work/layout.json` records each page's before and after order and the outcome of
each crop repair. The canonical page records in `source/pages/` carry the
resulting order and block flags. Missing geometry or an invalid or unavailable
judgment keeps provider order with `layout_fallback`. Blocks with unresolved
issues, including explicitly identified handwriting, keep their ID and carry
`quarantined` for assembly to exclude from lesson content.

## Translate planned units

The `translate` checkpoint writes `<lang>/segments.json` and writes
`work/pivot.en.json` when a shared English pivot is used. The language registry
in `src/bodhan_lesson/backend/config.py` uses the recipe's base language codes and lists
Indic-Speak production and preview tiers and the hosted voice catalog.
[Bodhan API reference](https://console.bodhan.ai/api-docs/)
[Indic-Speak model card](https://huggingface.co/bodhan-ai/indic-speak#languages-supported)

For hosted OCR and translation, set `BODHAN_OCR_KEY` and
`BODHAN_TRANSLATE_KEY` in the environment or `.env` and run with
`BODHAN_BACKEND=hosted`. Hosted translation uses Bodhan's OpenAI-compatible
`/v1/chat/completions` endpoint with the `indic-translate` model.
[Bodhan quickstart](https://console.bodhan.ai/#quickstart)

For local translation, add the vendor MT inference extra to the GPU
environment created above.

```bash
uv pip install --python .local/bodhan_genai/.venv/bin/python -e '.local/bodhan_genai[mt-infer]' -e .
```

The local adapter loads the official BF16 Indic-Translate weights on CUDA.
The model can exceed the available GPU memory on systems that run local OCR.
A confirmed CUDA OOM follows the same hosted fallback policy as OCR.
[Bodhan IndicTranslate SDK](https://github.com/Bodhan-AI/bodhan_genai/tree/main/src/bodhan_genai/mt)

### Optional 4-bit GGUF translation

For smaller GPUs, use the 5.30 GB community Q4 conversion of Bodhan Indic-Translate
with a recent CUDA-enabled build of llama.cpp. Start this from a terminal with
`llama-server` on its path. The first start downloads the GGUF weights.
[GGUF model card](https://huggingface.co/Bonenk/indic-translate-GGUF) · [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases)

```bash
llama-server -hf Bonenk/indic-translate-GGUF:Q4_K_M \
  --alias indic-translate-q4 --host 127.0.0.1 --port 8080 \
  --ctx-size 4096 --parallel 1 --n-gpu-layers auto --jinja --reasoning off \
  --sleep-idle-seconds 10
```

When OCR and translation share a GPU, wait for the server to sleep so OCR can
use the GPU first. `GET /props` reports `"is_sleeping": true` while the
model is unloaded. The first translation request wakes it after the recipe has
closed the local OCR model. If the GGUF model needs more GPU headroom when it
wakes, restart the server with fewer GPU layers, or use `--n-gpu-layers 0`
for CPU inference. [llama.cpp sleep mode](https://github.com/ggml-org/llama.cpp/tree/master/tools/server#sleeping-on-idle)

```bash
curl -fsS http://127.0.0.1:8080/props | python -m json.tool
BODHAN_BACKEND=local-first BODHAN_TRANSLATE_GGUF_URL=http://127.0.0.1:8080 \
  BODHAN_OCR_KEY= BODHAN_TRANSLATE_KEY= \
  .local/bodhan_genai/.venv/bin/python -m bodhan_lesson \
  --input .local/stage3-sample --source-lang en --langs hi \
  --out .local/stage3-gguf --until translate
python -m json.tool .local/stage3-gguf/hi/segments.json
```

Successful GGUF attempts report `backend: "local"` and
`model: "indic-translate-q4"`. Setting empty hosted keys in the command
prevents Bodhan API use if either local model runs out of CUDA memory.

An optional glossary can specify an English rendering under `en` to match the
term in a pivot. If there is no matching pivot term, the checkpoint checks the
target output for the requested wording and records any miss.

### Check and render translations

Run with `--until translation-qa` to write `<lang>/lesson.<lang>.md` and
`<lang>/translation-qa.json`. The lesson uses the canonical heading levels,
list numbering, and table shape. The QA file records per-unit discrepancies and
the outcome of one content repair through Indic-Translate. When a unit remains
unavailable, its slot stays visible in the lesson and its QA record is ineligible
for narration.

### Build narration scripts

Use `--until scripts` to write `<lang>/narration_script.<lang>.json` for each
target. The script records the exact speech text, source IDs, and reasons for
skips. Audio duration and speech QA start as null.

`BODHAN_SPEECH_MAX_CHARS` sets the per-chunk character limit and defaults to
300. The splitter prefers sentence boundaries and uses clause boundaries when
a sentence is too long. Equations stay in one chunk. A unit that cannot fit
safely is recorded as a skip.

`--voices voices.yaml` accepts a mapping such as `hi: Suhani`. The script
carries the requested name for validation during speech synthesis. The CLI
reports an explicit error for `--code-mixed`.

### Synthesize speech candidates

Use `--until speech` to write `<lang>/chunks.json` and candidate WAVs in
`<lang>/work/audio/`. The index links each candidate to its script chunk and
model attempts. Candidate paths are relative to the output directory. Failed
chunks carry a reason, and successful candidates from the same run are kept.

Hosted speech uses `BODHAN_SPEAK_KEY` with the preceding stages' credentials.
Voice names are case-sensitive. Choose a voice recorded for the target
language. English can use any voice available on the selected backend.
Hindi defaults to `Kavya` in both modes. The hosted catalog also offers
`Suhani`. [Hosted Indic-Speak API](https://console.bodhan.ai/api-docs/#text-to-speech-api)
· [Open checkpoint voice catalog](https://huggingface.co/bodhan-ai/indic-speak#voices)

For local speech, add the optional TTS inference extra to the Bodhan GPU
environment described above.

```bash
uv pip install --python .local/bodhan_genai/.venv/bin/python -e '.local/bodhan_genai[tts-infer]' -e .
```

The open weights load on the first synthesis request. Confirmed CUDA OOM
falls back for the affected chunk using the same text and voice. Set
`BODHAN_SPEAK_KEY` to enable that hosted request.

### Verify speech candidates

Run with `--until speech-qa` and reuse the same input and output paths to
continue from a completed `speech` checkpoint. Hosted transcription uses
`BODHAN_TRANSCRIBE_KEY`. The checkpoint writes `<lang>/speech-qa.json` with
raw scripts and transcripts, normalized strings, aligned edits, repair
attempts, candidate IDs and measured coverage.
[Hosted Indic-Transcribe API](https://console.bodhan.ai/api-docs/#speech-to-text-api)

`BODHAN_SPEECH_CER_THRESHOLD` defaults to `0.15` and accepts values from
`0` to `1`. QA case-folds, normalizes to NFC and collapses whitespace. It
ignores punctuation outside numeric separators, attached minus signs and
internal apostrophes. Indic marks, digits and negative words stay in the
comparison. Whole-word omissions, adjacent repetitions, numeric changes and
changes to recognized negation words can trigger repair below the CER threshold.

For Tamil, QA recognizes selected native and transliterated readings of
numbers, section references, degrees and angle labels. Raw transcripts stay
visible, and changed values or recognized angle labels still trigger repair.
Scripts translate supported geometry notation into speech using Indic-Translate
with protected values and labels. Tamil chunks prefer a safe split at about
180 characters before falling back to the configured limit. Adjacent plain
prose slices from the same paragraph can share a request of up to 240 characters
within the configured limit. The original punctuation stays available for QA
splits.

Suspect audio gets one full regeneration and, where the script offers a safe
punctuation boundary, one split into two shorter chunks. Every part must pass
for the split to be accepted. Aggregate CER divides accepted edits by accepted
normalized reference characters. Coverage counts the original reference
characters represented by accepted chunks. Unavailable measurements are
reported as null with a reason.

When resuming a compatible Tamil run from the previous QA policy, the runner
rechecks the saved transcripts before making any new calls. It can retry
omitted asides with spoken punctuation, or replace a rejected split part
while reusing its verified sibling.

For local verification, add the optional ASR inference extra to the Bodhan GPU
environment. The adapter loads Indic-Transcribe Core on demand.

```bash
uv pip install --python .local/bodhan_genai/.venv/bin/python -e '.local/bodhan_genai[asr-infer]' -e .
```

### Export section and lesson MP3s

Install FFmpeg with `libmp3lame` and make `ffmpeg` and `ffprobe` available on
`PATH`. Use `--until audio` to write section MP3s in `<lang>/narration/` and
`<lang>/lesson.<lang>.mp3`. Sections with usable verified chunks can export
partially. The script JSON records skipped sections, omitted chunks, QA coverage, encoded
durations and measured loudness. The target is −16 LUFS with a ±1 LUFS window.
Silence and clips below the measurement gate have an explicit unavailable result.

The lesson uses one MP3 encode of the normalized sections. Chapter offsets
come from accumulated PCM frames and are checked against the final file along
with chapter titles and AI-generated attribution. The timing tolerance is
80 ms for the encoded MP3 and millisecond chapter tags.

## Focused offline tests

```bash
source .venv/bin/activate
python -m pytest -q tests/stage1/test_language.py
python -m pytest -q tests/stage1/test_preprocess.py
python -m pytest -q tests/stage1/test_ocr.py
python -m pytest -q tests/stage1/test_layout.py
```

The [synthetic fixture generator](../../tools/make_sample.py) is available for developer use.
