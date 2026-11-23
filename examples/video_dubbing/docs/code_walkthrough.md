# Code walkthrough — learning and debrief guide

This is the living explanation of our video dubbing code. It covers what exists, why we wrote it, and how the Python syntax works. Update it after every feature implementation, including changes to earlier code and tests. Keep syntax notes short; spend more space on purpose, data flow, trade-offs, and limitations.

Latest full verification: **448 tests passed** with the authorized local speech fixture, plus a clean recipe validator and dependency consistency check.

Current status (November 21): the required CLI, measured fitting, loudness, subtitles and MP4 rendering are implemented. All 36 connected language/group cases fit with no skipped narration, no speech overlaps and speed at most 1.2×. Source sync is estimated; translation/naturalness fixes remain deferred. The current demo is `outputs/dubbed-demo-complete/`. Older sections below are historical evidence, not current authorization or status.

Paths below are relative to `examples/video_dubbing/`. We explain code we own, not the internal implementation of installed third-party libraries. The earlier temporary API probe scripts are experiments outside the submitted package; their findings are described separately near the end.

## 1. Start with the execution story

```sh
python -m bodhan_dub \
  --input /path/to/video.mp4 \
  --langs hi,ta,bn \
  --out outputs/prepare-run \
  --prepare-only
```

**Syntax, briefly:** `python -m` runs a Python module/package. Items beginning with `--` are named command-line options. A shell backslash continues a command on the next line. These example paths must be replaced with real paths; quote paths containing spaces.

**Purpose:** this is an explicitly limited preparation run. It opens the input, inspects its streams, extracts a predictable audio file, checks the result, and writes a report. The special flag prevents us from confusing a completed media-preparation step with a completed dubbing task. There are no model calls or translated outputs at this stage.

The path through our code is:

```text
Python finds the installed bodhan_dub package
  → __main__.py: main()
  → parse and validate command-line options
  → find ffprobe and ffmpeg
  → media.py: inspect_video()
      → run ffprobe → parse JSON → construct MediaInfo
  → media.py: extract_audio()
      → run ffmpeg → open resulting WAV → check format/duration
  → write report.json → return an exit status
```

The main data objects change along the way: command-line strings become `Path`/number values, ffprobe JSON becomes Python dictionaries/lists, selected metadata becomes a `MediaInfo` object, and the final report becomes JSON on disk.

## 2. File map

| File | Responsibility |
| --- | --- |
| `src/bodhan_dub/__init__.py` | Marks the package and supplies a short package description; no processing logic. |
| `src/bodhan_dub/__main__.py` | Command-line entry point, stage orchestration, and preparation report. |
| `src/bodhan_dub/media.py` | Reusable local media inspection/extraction functions and structured errors. |
| `src/bodhan_dub/vad.py` | CPU speech detection, settings, interval validation, and gap derivation. |
| `tests/test_vad.py` | Settings/timeline tests and actual detector integration tests. |
| `scripts/setup_local_media.py` | Optional explicit download/link setup inside a virtual environment. |
| `tests/test_media.py` | Tests our behavior using synthetic inputs and actual media executables. |
| `pyproject.toml` | Package/build metadata, source discovery, and pytest configuration. |
| `requirements.txt` | Pinned packaging/test-runner dependencies for the current slice. |
| `requirements-media-tools.txt` | Snapshot of the optional local media-bootstrap environment, including indirect dependencies. |
| `.gitignore` | Keeps credentials, environments, caches, and generated media out of normal Git tracking. |
| `.env.example` | Variable names with placeholders, not real credentials. |
| `docs/media_compatibility.json` | Evidence: tested platform, actual binary versions/hashes, and demo metadata. |

## 3. Python syntax used throughout

| Syntax | Brief meaning |
| --- | --- |
| `import json` | Make a module available under its name. |
| `from pathlib import Path` | Import a particular object from a module. |
| `from .media import ...` | Import from another module in the same package. |
| `def f(x: str) -> int:` | Define a function with type annotations. Annotations describe intended types; they do not automatically validate values. |
| `str \| None` | Value may be a string or `None`. This syntax requires Python 3.10+. |
| `list[str]` | A list expected to contain strings. |
| `value=None` | Default argument used when the caller omits it. |
| `if not value:` | Execute when a value is false/empty/missing. |
| `a or b` | Choose `a` when truthy, otherwise `b`. |
| `f'...{value}...'` | Insert a value into a formatted string. |
| `{'key': value}` / `[a, b]` | Dictionary with named values / ordered list. |
| `obj.get('key', fallback)` | Read a dictionary key with a fallback if absent. A present `None` is still `None`. |
| `[x for x in items if condition]` | Build a list from items satisfying a condition. |
| `try` / `except` | Catch specified failures and handle/translate them. |
| `raise Error(...) from exc` | Raise an error while retaining the original cause for debugging. |
| `with resource as x:` | Use a resource with automatic cleanup when leaving the block. |
| `Path(...) / 'file.wav'` | Join filesystem path components; `/` here is not arithmetic division. |
| `if __name__ == '__main__':` | Run a block when this file/module is the program entry point. |

## 4. `media.py`: errors and the media description

### Imports

`dataclass`/`asdict` create and serialize a small metadata object. `json` handles ffprobe output. `math` rejects invalid numeric values. `Path` handles file locations. `shutil` locates executables. `subprocess` runs ffmpeg/ffprobe. `wave` reads WAV headers and samples. These are Python standard-library modules, so our current runtime processing has no third-party Python dependency.

### `MediaError`

```python
class MediaError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
```

**Syntax:** inheritance makes this a specialized exception. `self` refers to this error instance. `super()` initializes the parent exception. `self.code` adds an attribute.

**Purpose:** callers need both a stable machine-readable category and a useful human-readable explanation. `missing_audio` can drive behavior without searching an English sentence for keywords. The normal exception message remains readable in logs and reports. Media functions raise this error; the CLI catches it and reports failure without pretending preparation succeeded.

### `MediaInfo`

**Syntax:** `@dataclass(frozen=True)` generates an initializer and useful object behavior; `frozen=True` prevents ordinary field reassignment after construction.

**Purpose:** inspection happens once and hands a clear metadata contract to extraction. A dictionary could accidentally use inconsistent key names; named fields make this boundary easier to follow. Immutability reduces the risk that a later step silently changes the timeline we inspected.

| Field | Why it is retained |
| --- | --- |
| `source` | Absolute source path used by ffmpeg. |
| `duration_seconds` | Target length of the extracted audio timeline. |
| `video_start_seconds` | Timestamp origin of the selected video stream. |
| `audio_start_seconds` | Timestamp of the selected audio stream's start. |
| `video_stream_index` / `audio_stream_index` | Identify selected streams within the container. |
| `audio_channels` / `audio_sample_rate` | Describe the source audio; these are not the normalized output settings. |

## 5. `media.py`: locating and running tools

### `tool_path(name, explicit=None)`

**Syntax:** `shutil.which(explicit or name)` searches for an executable. `None` means the user supplied no override.

**Purpose:** use an installed tool or a user-supplied executable path. Runtime processing must not unexpectedly download programs. If the dependency is unavailable, return an actionable error explaining installation or the relevant path option. This keeps evaluator machines independent of our local directory layout.

### `run_media(command, timeout)`

**Syntax:** `subprocess.run` receives a list of command arguments, captures output, decodes it as text, and waits up to the specified timeout. `check=False` lets us handle a nonzero return code ourselves. No shell is requested.

**Purpose:** one shared boundary gives media subprocesses consistent timeout/error behavior. An argument list keeps a filename containing spaces as a single argument and avoids treating filenames as shell commands. A subprocess start failure differs from a tool reporting corrupt media, so the error categories distinguish them.

On a nonzero return code, the last 1,000 characters of stderr are included for diagnosis. This is a bounded amount of diagnostic text, not a full transcript. It may include local filenames and is not a general-purpose sanitizer for arbitrary future tools. API keys are not passed to these media commands. Successful callers receive stdout, which is especially useful for ffprobe JSON.

## 6. `media.py`: interpreting metadata

### `_number(value, default=None)`

**Syntax:** a leading underscore signals an internal helper by convention. `float(value)` attempts conversion; `math.isfinite` rejects infinity and NaN. `pass` means no action in that exception branch.

**Purpose:** media metadata often stores numbers as strings and may omit values or use nonnumeric markers. Arithmetic on these values without validation could corrupt timings. This helper converts acceptable values, uses an explicit fallback when supplied, or raises a structured error. The caller still needs to reject negative or zero durations where inappropriate.

### `inspect_video(...)`: step by step

1. **Normalize the path.** `expanduser()` expands a leading home-directory shorthand; `resolve()` produces an absolute resolved path. `is_file()` rejects missing paths/directories.
2. **Check the extension.** `suffix.lower()` allows case-insensitive supported names. This is only an early filter: ffprobe must still successfully read the content.
3. **Run ffprobe.** Request stream metadata and container metadata as JSON. A stream is a separate video/audio track inside the file, not a time chunk.
4. **Parse and check structure.** `json.loads` turns JSON text into Python objects. We require a stream list and inspect stream types. Attached cover pictures do not count as the main video.
5. **Select streams.** The initial documented policy uses the first playable video and first audio stream. It does not identify which of several tracks is English, nor offer multitrack selection yet.
6. **Extract timings and format.** Prefer stream-level start/duration values. When unavailable, fall back to container timing information. Convert channels/rate/index values to integers. Missing/bad metadata is an error.
7. **Validate the duration.** Normal input must be 60–900 seconds. Short synthetic clips require `allow_short=True`; the upper limit still applies. That exception exists for controlled tests, not a claim that a test clip is the required full demo.
8. **Return `MediaInfo`.** Extraction receives already-selected, checked metadata rather than reparsing raw JSON.

**Timing fallback in the current code:** when stream duration is missing, the code uses container duration plus container start minus selected-video start. This is a metadata-based estimate; container timestamp conventions can vary. The current tests and demo do not establish correctness for every unusual/nonzero-origin or variable-frame-rate file. Such cases need targeted fixtures before making stronger claims.

**Useful syntax detail:** Python evaluates function arguments before calling the function. Therefore nested `_number(...)` fallback expressions are evaluated even when the preferred value is present. We make those nested fallbacks tolerant rather than assuming they are lazily evaluated.

## 7. `media.py`: extracting the audio

### Why normalize to one format?

`extract_audio(...)` creates 16 kHz, mono, 16-bit PCM WAV. That gives the later detector/transcription stages a predictable input instead of forcing them to handle arbitrary codecs, channel layouts, and rates. This is decoding/resampling, not transcription, translation, or denoising. The original media remains available for later background work; a normalized analysis copy is not the final dubbed soundtrack.

### Why care about timestamps during extraction?

Suppose a video begins at time zero but its audio starts one second later. Removing that initial quiet second would shift later speech too early. We keep the audio aligned to the selected video's first frame and store the original offset. No speech detector runs in this feature.

### The filter chain

```text
asetpts=PTS-(video_start_seconds)/TB,
aresample=16000:async=1:first_pts=0,
apad
```

**Syntax:** this is an ffmpeg filter expression, not Python. Python constructs it with an f-string. Commas chain filters. `PTS` refers to presentation timestamps and `TB` to their time base.

**Purpose:** shift audio timestamps relative to the video origin, resample while accounting for timing and starting at zero, then make silence available for padding. Combined with the output duration limit, this keeps short audio from producing a shorter analysis timeline. The delayed-audio test checks that an initial gap remains silent and later audio samples survive.

### Important ffmpeg arguments

| Argument | Brief meaning and purpose |
| --- | --- |
| `-hide_banner`, `-loglevel error` | Reduce routine output so failures remain visible. |
| `-nostdin` | Do not wait for interactive keyboard input during unattended runs. |
| `-n` | Refuse to overwrite an existing output file. |
| `-copyts` | Retain input timestamps for our explicit timeline handling. |
| `-i` | Specify the input file. |
| `-map 0:<audio index>` | Select the inspected audio stream from the first input. |
| `-vn` | Do not include video in the analysis WAV. |
| `-af` | Apply the audio filter chain. |
| `-t` | Bound the output duration to the selected video duration. |
| `-ac 1`, `-ar 16000` | Mono output at 16,000 samples per second. |
| `-c:a pcm_s16le` | Uncompressed signed 16-bit little-endian audio samples. |

### Verifying the result

**Syntax:** `with wave.open(...) as audio` closes the file automatically. Frames divided by sample rate give duration. A tuple comparison checks rate, channel count, and sample width together. `abs(actual - expected)` measures timing difference regardless of its sign.

**Purpose:** a successful ffmpeg exit is not enough. We open what it produced and verify the requested audio representation and duration. The 0.01-second tolerance here is an internal extraction check, not the assignment's separate final-video tolerance. A duration mismatch is reported as failure. Failed extraction may leave a partial file; we do not mark it accepted, and reruns must use a fresh output folder.

The returned dictionary includes the audio path, duration, format, timeline origin, and source offset. It has no recognized words or speech boundaries.

### `describe(info)`

**Syntax:** `asdict(info)` turns a dataclass into a dictionary.

**Purpose:** JSON understands ordinary dictionaries/numbers/strings, not our custom `MediaInfo` instance directly. This is the conversion between an internal object and a report field.

## 8. `__main__.py`: command-line orchestration

### `main(argv=None)` and argument parsing

**Syntax:** `argparse.ArgumentParser` creates the CLI. `required=True` makes an option mandatory, `type=Path`/`type=float` convert values, and `action='store_true'` makes a flag default to false and become true when present. With `argv=None`, argparse reads the process command line; tests can pass a list explicitly.

**Purpose:** keep the external interface predictable and testable. We reserve the assignment's input/languages/output arguments while honestly limiting current behavior to preparation. Omitting `--prepare-only` is an error; returning a complete dubbing success before that pipeline exists would be misleading.

The comma-separated language option is split and stripped into a list. Currently we only require that it is nonempty; production-language validation is pending. These codes are recorded but cause no translation calls yet.

### Guardrails before work

We reject nonpositive/infinite/NaN timeouts. An existing nonempty output directory is refused so an accidental rerun does not overwrite previous media or reports. We create the destination if needed and report directory-creation failures. Input/media checks then happen through the reusable functions.

Argument errors use argparse's exit status 2 and happen before a run report exists. Runtime/media failures generally use status 1 and write a report when output access permits. This distinction matters: not every invalid invocation can produce an output folder/report. Some filesystem errors during initial path inspection still need broader hardening; we should not describe error reporting as universal.

### Report and stage progression

```text
preparing → inspect completed → extract_audio completed → prepared
                       failure → failed
```

**Purpose:** the report makes completion boundaries explicit. `completed_stages` lets the user see how far the run got. `model_calls` and `cost_inr` are zero because this slice makes no model calls, not because future dubbing will be free. `dubbing_complete` stays false even after successful extraction.

`time.monotonic()` measures elapsed time using a clock intended for durations, so a wall-clock adjustment is less likely to distort the measurement. The report is written as indented JSON; a short JSON summary is printed for the caller. This is not yet the full assignment report schema or structured per-segment logging.

### Error and exit flow

`except MediaError` preserves our error category and message. The broader filesystem handler covers later file access/write errors without exposing a Python traceback as the normal user experience. If the report itself cannot be written, the CLI writes an explanation to stderr and exits nonzero.

`return 0` indicates successful preparation; `return 1` indicates runtime failure. `raise SystemExit(main())` passes that integer back to the shell. Tests can call `main(arguments)` directly without launching another Python process.

### `__init__.py`

The current file contains only a package docstring describing media preparation and local detection. It performs no imports, initialization, network calls, or work. Keep it free of surprising side effects.

## 9. `setup_local_media.py`: explicit local tooling

**Purpose:** support development on a machine without system ffmpeg/ffprobe, while keeping normal video processing offline with respect to tool downloads. This helper is optional; an evaluator can install the media executables through their system package manager instead.

The script performs these steps:

1. Check that it is running inside a virtual environment on a POSIX system. `sys.prefix` names the active environment; equality with `sys.base_prefix` generally means there is no venv. `os.name` identifies a broad platform family.
2. Import the optional helper package. If absent, give its pinned requirement-file instruction.
3. Explicitly fetch the platform's media executables if not already downloaded. This setup step requires network access; the processing module does not do this.
4. Build paths inside the virtual environment's `bin` directory and create symbolic links to the downloaded executables. A symlink is a filesystem reference, not a second executable copy.
5. If a link already points to the intended executable, `continue` skips unnecessary work. If something else already occupies the path, refuse to overwrite it.

The helper package advertised ffmpeg 8, but our actual downloaded binary identified as 7.0. We recorded actual versions and SHA-256 hashes. A hash identifies the tested bytes; it is not currently a download-time integrity check. Because the helper uses an upstream-mutable URL, package pins alone do not fully reproduce the binary download. Do not claim a fully locked binary toolchain yet.

## 10. `test_media.py`: what each test teaches us

### Test syntax and fixtures

**Syntax:** `@pytest.fixture` provides reusable setup to tests naming that argument. `scope='module'` shares it across this file's tests. `tmp_path` is pytest's isolated temporary directory. `@pytest.mark.parametrize` runs one test body for each listed input. `assert` states the expected result. `pytest.raises` verifies that a particular exception occurs. `pytest.approx` compares approximate numeric values rather than requiring identical floating-point representations.

**Purpose:** tests should check observable behavior, not merely repeat the implementation. We create small local videos from ffmpeg's synthetic color/tone sources, avoiding committed recordings, copyright issues, paid calls, and dependency on a provider's current response. A tone is not human speech; these tests prove media handling, not VAD accuracy or dubbing quality.

The `tools` fixture finds real executables. If absent, media integration tests are skipped, not passed. A green run with skips is weaker evidence than our recorded local run with all 21 passing.

`make_clip` creates a small video and optionally audio; its `*` makes following arguments keyword-only. An audio offset creates a known initial gap for alignment checks. `subprocess.run(..., check=True)` makes fixture-generation errors fail immediately instead of testing against nonexistent media.

| Test | Purpose and evidence |
| --- | --- |
| `test_missing_file` | Missing paths fail for all four supported suffixes before a media process is launched. |
| `test_reject_non_video_extension` | An audio-only file suffix is rejected early. |
| `test_corrupt_video` | A supported filename is not enough; actual unreadable content fails. |
| `test_no_audio` | A real video with no audio produces the right error category. |
| `test_short_fixture_requires_opt_in` | Production duration validation stays active unless the explicit test exception is requested. |
| `test_supported_containers` | Actually encode, inspect, and extract MP4/MOV/MKV/WebM; this goes beyond checking strings. |
| `test_extraction_preserves_duration_and_leading_gap` | Check output format/duration, unchanged source bytes, retained leading silence, surviving later samples, and refusal to overwrite. Runs with and without delay. |
| `test_prepare_command_and_non_overwrite` | Exercise CLI orchestration, honest prepared/not-dubbed reporting, zero calls, and rerun protection. |
| `test_bad_input_writes_failure_report` | Recoverable media failure produces an inspectable report and nonzero status. |
| `test_no_full_dubbing_success_claim` | Full dubbing is rejected explicitly while unimplemented. |
| `test_reject_invalid_timeout` | Zero, negative, NaN, and infinite timeouts fail argument validation. |

Parameterization expands these functions into **21 test cases**. These are not the required full 30-second end-to-end dubbing smoke test; that remains future work. All-container testing on one Mac also does not establish clean-container or cross-platform compatibility.

## 11. Packaging, dependencies, and credentials

### `pyproject.toml`

**Syntax:** TOML groups settings using `[section]` headings, `key = value`, and lists in square brackets.

**Purpose:** `[build-system]` selects the package builder. `[project]` supplies package identity and the minimum Python version. The source finder tells setuptools to find packages beneath `src/`. Pytest's section points it at `tests/`.

The project distribution name and import package name serve different roles: installing `bodhan-video-dubbing` makes `bodhan_dub` importable. `pip install -e .` installs an editable link so source edits are immediately visible without reinstalling every change. `--no-build-isolation` uses the build tools already installed in the venv; `--no-deps` skips automatic dependency resolution for that installation command, so prerequisites must have been installed beforehand.

The media module uses standard-library Python plus external ffmpeg/ffprobe. Feature 2 adds pinned Silero, PyTorch, NumPy, and a compatible NetworkX constraint to the runtime dependency list; their purposes are explained below. The full project is not complete just because its metadata installs successfully.

### Requirement files

`name==version` pins a Python package release. The base file now includes setuptools, pytest, detector/runtime dependencies, and pinned indirect dependencies. The optional media-tools file records the installed helper environment, including indirect dependencies the helper pulled in. Pinning versions is useful but does not lock external media binaries, guarantee availability on every Python/platform, or pin the Python interpreter itself.

### `.gitignore`

`.env` and local environments are excluded so secrets and installed packages do not enter the recipe. `outputs/*` excludes generated files; `!outputs/.gitkeep` re-includes the tiny placeholder preserving the folder. The same pattern is used for sample data. `*` matches a pattern; `!` reverses an earlier ignore rule.

Ignoring a file does not encrypt it, prevent local reading, or remove it if it was previously tracked. No assistant commits or pushes have been made. Media outputs and the real root `.env` were verified ignored.

### `.env.example` and the real `.env`

The example contains placeholder names only. The actual root `.env` belongs to the user; this guide neither contains nor reads its values. This feature does not load credentials. Model-specific keys and portable LLM configuration will be used by later approved features. OCR is optional for the selected assignment. The generic Bodhan key placeholder exists for Cookbook-validator compatibility and is not the application's selected authentication scheme.

## 12. Evidence, experiments, and current limits

The supplied demo was inspected as approximately 329.27 seconds. Its audio extracted to the expected format with a matching duration and no model calls. `docs/media_compatibility.json` records evidence; it is a record, not executable logic or a universal compatibility promise. `report.json` in an ignored preparation-output folder describes that run.

Earlier temporary probe scripts loaded the user-provided configuration, made explicitly authorized short API requests, and saved sanitized results under ignored outputs. They tested network contracts, not this media feature. Those scripts are not installed-package entry points or a reusable model-client implementation. They showed:

- Groq could return the expected planning function call; the proposed function was not executed.
- Bodhan ASR returned text but no timestamps in the tested responses.
- Synthesis returned WAV bytes despite an audio/mpeg label.
- The plain translation route returned 404, while the chat route worked with Bodhan's exact model prompt format. An earlier nonconforming prompt echoed English and was rejected as a translation result.
- One Hindi back-transcription matched its synthesis text after normalization. That is a tiny test result, not a broad language-quality claim.

Known overall limits: first-stream selection only; no word alignment; estimated VAD boundaries only; metadata-based duration fallback; limited unusual-timestamp coverage; some early filesystem errors are not yet normalized; extraction failures may leave partial outputs; no clean-container validation yet. These should be improved through discussed features or targeted fixes, not hidden in documentation.

## Feature 2 — local speech detection

### What changed in the execution story

There are now two explicit modes. `--prepare-only` still stops after extraction. `--detect-speech-only` runs extraction and then CPU Silero VAD, writing `speech_regions.json` and adding a detection summary to `report.json`. The flags are mutually exclusive. Neither mode transcribes, translates, synthesizes, or produces a final dubbed video.

```text
Input video → inspect → aligned WAV
                           ├─ prepare-only: report prepared
                           └─ detect-speech-only: local VAD → speech regions + gaps → report speech_detected
```

The report's legacy `model_calls` field counts hosted/API model calls and remains zero. `local_vad_runs` distinguishes the one local model inference pass. We should retain this distinction as the report schema develops, rather than claiming no model was used.

### Why use VAD here?

Our earlier Bodhan probe returned words but no timestamps. A detector provides estimated start/end ranges for speech, allowing us to locate later ASR chunks on the source timeline. It does not recognize words, assign speaker identities, identify sentence boundaries, or separate music. This role is explicitly permitted by the brief.

We use Silero's packaged model through its loader, not `torch.hub`, so processing does not fetch a repository or weights over the network. PyTorch runs on CPU with one thread. Standard-library WAV reading replaces additional audio-decoder libraries because the preparation stage already gives us the expected format.

### New dependencies and their purpose

| Dependency | Role |
| --- | --- |
| `silero-vad==6.2.3` | Packaged speech detector and timestamp postprocessing helpers. |
| `torch==2.8.0` | CPU tensor operations and execution of the detector model. |
| `numpy==2.2.6` | Efficient conversion of PCM bytes into normalized numeric samples. |
| `networkx==3.4.2` | PyTorch dependency constrained to a release compatible with declared Python 3.10 support. |

Other indirect dependency pins are listed in `requirements.txt`. The project runtime dependency list is no longer empty. Installation/runtime have been tested on our Mac's Python 3.12, not every allowed interpreter/platform. On Linux, an explicitly CPU-only PyTorch wheel installation can avoid the default distribution's GPU-related downloads; execution itself does not require a GPU.

### `VADSettings`: visible, validated detector configuration

**Syntax:** a frozen dataclass stores four settings. `__post_init__` runs after its generated initializer. `getattr(self, name)` reads an attribute using a variable name. `type(value) is int` deliberately rejects fractional values and booleans for millisecond durations.

**Purpose:** thresholds should be recorded, configurable starting points rather than hidden constants or claims of universally optimal settings. Invalid values fail before extraction/inference begins.

| Setting | Default | Meaning |
| --- | --- | --- |
| `threshold` | 0.5 | Detection threshold passed to the model's timestamp helper; this is not a guarantee of calibrated confidence. |
| `min_speech_ms` | 150 | Minimum candidate speech length used by postprocessing; can miss very brief utterances. |
| `min_silence_ms` | 300 | Silence duration used to separate candidate regions; shorter pauses may stay within a region. |
| `speech_pad_ms` | 50 | Context padding around regions, subject to upstream clipping/adjacency handling. |

The threshold must be finite and strictly between zero and one. Millisecond settings must be integers within 0–10,000; minimum speech/silence must be positive, while zero padding is allowed. These are our validation/configuration choices, not extra assignment requirements. Their practical accuracy still needs listening and harder fixtures.

### `timeline_regions(spans, frames)`: validate timing and preserve gaps

**Syntax:** returns two lists as a tuple, unpacked with `speech, gaps = ...`. A nested function builds repeated region dictionaries. `f'speech-{n:04d}'` produces a stable four-digit identifier. Chained comparisons validate ordered bounds. Division by `SAMPLE_RATE` converts sample indices to seconds.

**Purpose:** reject impossible model outputs instead of quietly sorting or repairing them and concealing a bug. Each region must start after the previous region ends, end after it starts, and remain within the WAV's sample count. Bounds must be integers. Overlapping/unsorted intervals cause a structured error.

An interval includes its start sample but excludes its end sample. That convention makes adjacent intervals fit exactly without duplicate samples. The cursor tracks where the previous speech ended. Any space before the next speech becomes a gap; the remainder after the last speech becomes a final gap. A no-speech result produces one gap spanning the entire recording.

Example: if two model intervals occupy seconds 1–3 and 5–7 in an eight-second audio file, gaps occupy 0–1, 3–5, and 7–8. Speech plus gaps partitions the timeline; the function never concatenates away silence. Seconds are calculated from integer sample offsets rather than independently rounded or guessed word timings.

These are model-estimated, padded speech intervals. A gap means “not classified as speech,” not proof of silence. It might contain background sound or missed speech. Regions can exceed 30 seconds: ASR-window cutting is a separate future feature.

### `detect_speech(...)`: read, infer, validate, describe

1. Use default settings if the caller did not provide them and begin elapsed-time measurement.
2. Open the prepared WAV, checking 16 kHz mono PCM16 with no WAV compression. Reject empty recordings or more than 15 minutes before loading a large waveform.
3. Read the frames and compare actual byte length with the declared sample count. A truncated file must not become a successful partial detection.
4. Import NumPy, PyTorch, and Silero lazily. Basic preparation can therefore run without importing the heavy model libraries. Missing packages get a useful dependency error.
5. Convert PCM bytes into a floating-point tensor, load the packaged model on CPU, and call its speech-timestamp helper with the recorded settings.
6. Validate returned integer intervals and construct their complementary gaps.
7. Return a structured result with model/runtime versions, configuration, timing, summary, warnings, and limitations.

Important conversion:

```python
samples = torch.from_numpy(
    np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
)
```

**Syntax:** `<i2` means little-endian signed integers occupying two bytes. `frombuffer` interprets bytes as numbers without decoding speech. `astype` creates float32 values, the division scales their range, and `from_numpy` creates a tensor from that numeric array.

**Purpose:** a PCM sample of 16,384 becomes approximately 0.5, and −32,768 becomes −1.0. Feeding raw integer magnitudes to a model expecting normalized amplitudes would be incorrect. This is numerical audio preparation, not language generation.

`torch.inference_mode()` avoids training-related gradient tracking. `.to('cpu')` specifies the device explicitly. `return_seconds=False` asks for sample offsets; our own helper consistently derives seconds. The call is a single local inference pass and does not consume Bodhan/Groq credits.

The broad exception handler around model execution converts runtime/model failures into a stable error code and retains the original exception as a cause. It must not be interpreted as an alternate detector fallback. Output validation remains separate, so invalid timing has its own error category.

### Output contract and limitations

`speech_regions.json` includes total samples, rate, video-origin timing, end-exclusive convention, detector/version/settings, speech intervals, gaps, summary durations, elapsed inference time, and warnings. Stable IDs help later stages refer to ranges without asking an LLM to invent timestamps.

An empty speech list is a valid detector result with an explicit warning. It does not assert that the source is certainly silent. A report status of `speech_detected` means that stage executed successfully, not that a human verified every boundary or that at least one region necessarily exists.

### CLI integration

**Syntax:** `add_mutually_exclusive_group()` prevents requesting both modes. New CLI duration options use `type=int`, while the threshold uses `type=float`. `VADSettings(...)` validates them before output creation. `**detection['summary']` expands dictionary entries into the report's summary dictionary.

**Purpose:** the existing input/extraction checks remain shared. Detection runs only when requested; preparation retains its original behavior. Writing the region JSON precedes marking detection completed. A detector failure leaves earlier completed stages in the report, preserving useful progress rather than claiming full success or silently continuing.

The full dubbing command is still rejected without one of these partial-stage flags. All target-language arguments are still only recorded; production support validation and actual language outputs remain pending.

### New tests and what they establish

| Test group | Purpose |
| --- | --- |
| Invalid threshold/duration settings | Reject out-of-range, NaN/infinite, zero-required, and fractional inputs. |
| Invalid model intervals | Reject negative, zero-length, beyond-file, fractional, overlapping, and missing bounds. |
| Gap complement | Ensure speech and gaps cover every sample exactly once and convert offsets consistently. |
| Wrong/empty WAV | Require the prepared input format rather than silently resample or accept missing samples. |
| Real-model silence with networking blocked | No speech is invented in the synthetic silence fixture, and installed packaged weights load without network access. |
| Real-model controlled pauses | Insert a permitted previously synthesized English sample twice between known silence intervals; check the two detected regions against expected placement within 300 ms. This is a fixture-specific assertion, not a general accuracy guarantee. |
| Detection CLI integration | Build a synthetic video with that audio, run extraction plus VAD, check the separate artifact and honest no-dubbing report. |

`monkeypatch.setattr(...)` temporarily replaces a network connection method during the offline test; pytest restores it afterward. A variadic `*args, **kwargs` stub accepts arbitrary arguments then deliberately raises if a connection is attempted. This checks ordinary Python socket connections, not every possible network mechanism in arbitrary native software.

The positive fixture is supplied using `BODHAN_TEST_SPEECH_WAV`. If no permitted speech sample is supplied, the two positive integration tests skip explicitly. No API calls generate fixtures during tests, and no recordings are committed. `tmp_path` contains the normalized sample and controlled timeline. The tone fixtures from Feature 1 are not presented as speech-accuracy tests.

### Recorded verification for Feature 2

- Full local suite: **44 passed**, with the positive speech fixture supplied and no skips in that run.
- Supplied 329.27-second demo: **39 estimated speech regions**, approximately **222.59 seconds classified as speech** and **106.68 seconds not classified as speech**.
- Longest detected demo region: approximately **19.14 seconds**. This does not establish that all future inputs produce <=30-second regions.
- Detector call on this machine: approximately **1.04 seconds**. Not a runtime promise for other inputs/machines or the complete pipeline.
- No paid API calls. Demo boundaries have not received a full listening/ground-truth review; background music/overlapping-speaker accuracy remains unestablished.

### What remains after detection

Next, discuss and approve safe ASR-window construction and Bodhan transcription. VAD ranges are not automatically complete sentence units, and long continuous speech still needs careful handling. Later grouping should preserve connected thoughts without claiming unmeasured word timing.


## Feature 3 — local clickable listening review

### Purpose and scope

The detector's 39 demo regions are estimates, not a known correct answer. We added a development review page so a person can listen to those regions and to gaps that might contain missed speech. It consumes existing output artifacts; no new detector run or paid model call is needed. It is not the optional final application UI and does not automatically score accuracy.

The page contains a full audio player, speech/gap/all filters, clickable time ranges and timeline, a playback clock, replay, and optional boundary buffers. Default selection playback starts at the saved start and stops at the saved end. Only an explicitly chosen buffer plays extra context. Full-recording playback deliberately begins at zero.

Files added:

| File | Role |
| --- | --- |
| `src/bodhan_dub/review.py` | Validate saved results and generate an offline HTML page. |
| `src/bodhan_dub/templates/__init__.py` | Package marker for local resources; no processing. |
| `src/bodhan_dub/templates/review.html` | HTML structure, CSS appearance, and browser playback logic. |
| `tests/test_review.py` | Seven generator/validation checks. |
| `tests/test_review_ui.cjs` | Optional Node-only playback simulation, without browser control or recording. |

### `create_review(run)` in Python

**Syntax:** `Path.resolve` normalizes a run path; `files('bodhan_dub.templates')` accesses an installed package resource. `json.dumps` serializes data. `target.open('x')` creates a new file exclusively, refusing to overwrite an existing one.

**Purpose:** keep page generation reproducible and separate from inference. The function expects a detection folder containing `speech_regions.json` and `source_audio.wav`. It validates metadata size, sample rate, frame count, and speech intervals; then it opens the WAV and checks format/length against the metadata. This catches stale or mismatched artifacts without decoding speech.

Display timings are rebuilt from integer sample offsets through the same timeline validator used by VAD. The page does not blindly trust edited seconds or supplied region IDs. Complementary gaps are derived again, preserving the timeline.

The generator embeds only the needed display data, not the user's entire report or environment. It escapes HTML-sensitive characters before inserting JSON into a script-data element. The UI places dynamic text through `textContent`, not interpreted HTML. These choices reduce injection hazards from metadata. They are not a general guarantee for any future added template content.

The audio link is the fixed relative filename `review_audio.mp3`, so the page must remain beside that file. The generator checks format and length, not a cryptographic audio-content match. No upload, credentials, model endpoint, or recording API is involved. After validation, local ffmpeg encodes a browser-friendly `review_audio.mp3` copy; the original WAV remains unchanged. Encoding is local media conversion, not speech synthesis. Existing page/playback files are not silently overwritten.

`main(argv)` parses an existing run folder, invokes generation, prints a small JSON result, and returns zero or a structured-error exit. A new `python -m bodhan_dub.review` entry point is separate from the pipeline CLI; the latter's required interface remains intact.

### Packaging the HTML

**Syntax:** `[tool.setuptools.package-data]` maps a package name to resource filename patterns.

**Purpose:** the HTML template must ship in a built distribution rather than work only because a developer happens to have the source tree nearby. `templates/*.html` includes the page with the package. This is static-file packaging, not a hosted Site manifest or deployment.

### HTML and CSS: what the browser displays

**Syntax:** `<audio controls src="...">` uses the browser's built-in media controls. IDs identify particular elements. Labels/ARIA attributes describe controls for keyboard and assistive use. CSS selectors associate elements/classes with appearance; grid/flex arrange them, and a media query adapts the layout for narrow screens.

**Purpose:** keep the review lightweight and usable without frameworks, a frontend build, or external assets. The timeline is a proportionally sized set of buttons, not an audio waveform. Blue means a speech estimate; gray means not classified speech, which does not prove silence. A marker shows current playback position.

Native audio controls can behave differently between browsers. The presence of an `<audio>` element and successful HTTP delivery do not prove that a specific browser will decode or play it correctly. In response to the user's permission preference, computer-controlled browser inspection/recording is stopped; manual playback checks belong to the user.

### JavaScript data and rendering

**Syntax:** `const` holds bindings that are not reassigned; `let` holds changing playback state. `...array` spreads array items; `({...r, kind:'speech'})` copies an object with a display label. `.map`, `.filter`, and `.sort` transform the display list. Arrow functions define short callbacks. Template literals use backticks and `${...}` to format text.

**Purpose:** combine speech and gaps into one ordered timeline while retaining separate filter views. The data is read from the embedded JSON rather than fetched from a remote endpoint. Each list item and timeline block is an ordinary button referencing one canonical region.

- `timeLabel` converts seconds to a readable minute/second/millisecond label; display rounding does not modify saved sample offsets.
- `title` builds a human-readable speech/gap label from canonical generated IDs.
- `renderList` rebuilds the filtered list, its time ranges and durations, and click listeners. An empty filter view explains that absence of detected regions is not proof of absence of speech.
- `markSelection` highlights the selected region in both list and timeline and updates its pressed state.
- Filter listeners change only the displayed category. They do not rerun detection or remove intervals.

### Playback state and exact seeking

The first version set `currentTime` and immediately called `play`. The user reported playback starting at zero instead of the selected timestamp. We cannot claim the exact browser cause was established, but that approach did not explicitly wait for metadata/seek readiness. The revised code does so and makes zero-buffer playback the default.

**Syntax:** `async` functions can `await` a Promise representing work that finishes later. `new Promise` supplies success/failure callbacks. Event listeners react to `loadedmetadata`, `seeked`, and `error`; `setTimeout` limits waiting. `finally` is not used here; explicit cleanup removes listeners and clears timeout handles.

- `waitForAudio(event, action, ready)` installs listeners before running an action, then resolves only when the relevant media state is ready. It also checks immediately in case readiness is already established. Errors/timeouts reject the wait and release listeners.
- `startPlayback(start, end, request, message)` invokes `play` synchronously during the click, initially muted, to retain browser user-gesture activation. It then waits for metadata/seek readiness, checks the requested position, and restores the previous mute setting only after seeking succeeds. A shared saved-mute state prevents overlapping requests from treating temporary mute as the user preference. It refuses to silently start at zero after seek failure. The 50 ms start-position check is a UI guard, not a claim of sample-accurate browser playback.
- `playRegion` records selection, calculates exact bounds or explicitly buffered bounds, pauses old playback, and hands those values to `startPlayback`.
- A monotonically increasing `playbackRequest` number invalidates an older asynchronous request when the user chooses something else. A stale completion must not overwrite the newer playback state. Old waits may remain until they resolve/time out, but cannot start outdated playback after their request is superseded.
- `stopAt` holds the selection's end; `checkStop` pauses when playback reaches it and updates the visible state. `timeupdate` and a short interval perform checks. Browser scheduling can delay callbacks, especially in background tabs; this is not sample-accurate editing or final dubbing timing.
- `clearStop` removes the selection stop interval without changing saved region data.
- `updateClock` refreshes the visible timestamp/playhead and performs the stop check.
- Replay uses the current selection. Full playback clears the region stop and seeks to zero intentionally.
- Changing the context buffer pauses the selection; replay applies the new value. Manually seeking beyond its end switches back to unrestricted playback. End/error listeners clean up state and display a message.

An event listener cannot guarantee browser autoplay permission. If seeking/loading/play fails, the page shows a failure rather than reporting that correct playback occurred. A media-load error also displays the expected same-folder requirement.

### Python tests

The generator tests check canonical timings/gaps, unchanged source artifacts, refusal to overwrite, a valid no-speech page, invalid intervals, mismatched audio, and missing files. These run as part of pytest. The latest complete suite has **60 passing cases**, including the delivery-server tests below with the positive speech fixture supplied. Without that fixture, the two earlier positive VAD tests still skip explicitly.

### Code-only JavaScript test

The Node test reads the generated HTML, extracts the data and script, and executes the page code against a minimal simulated DOM/media element. It does not control Safari, Chrome, the in-app browser, or the Mac desktop. It does not request microphone, screen, or recording access.

**Syntax:** CommonJS `require` loads Node's built-in `fs`, `vm`, and `assert` modules. `vm` evaluates the UI script in the supplied test context. A small test `Element` implements only the DOM operations our page uses. `Object.defineProperty` models media-time reads/writes, and `queueMicrotask` makes simulated metadata/seek completion asynchronous. An async wrapper waits for playback operations and reports assertion failures via a nonzero process exit.

**Purpose:** cover event-ordering mistakes that a synchronous fake player would miss. The simulation starts with metadata unavailable, then completes loading and seeking asynchronously. Assertions check the first actual play position against the selected timestamp, not merely the selected text label. It also checks filter counts, selected-region stopping, replay, exact zero-buffer behavior, full recording playback, context changes, and load-error messaging.

The script checks that the reviewed JavaScript contains no recording APIs or remote request URLs. That static check is limited to this source pattern; the stronger design fact is that the page has no recording/upload feature. The test cannot prove real browser decoding, audio output, seek fidelity, or perceptual boundary accuracy. Those remain manual checks.

### Review commands and operational limits

```sh
python -m bodhan_dub.review --run outputs/detect-demo
python -m bodhan_dub.serve_review --run outputs/detect-demo --port 8765
# Optional JS simulation, if Node is installed:
node tests/test_review_ui.cjs outputs/detect-demo/review_protected.html
```

The generator refuses to overwrite an existing page. During the explicit user-requested playback fix we updated the generated page on disk while preserving its embedded region data; the user's tab was not reloaded or controlled. Future template updates likewise need explicit regeneration/update of existing pages.

The local server is bound to loopback and scoped to the run folder, not the project root containing credentials. The page can also be opened directly from that folder where browser local-media policy permits. The simple preview server is development tooling, not a production app or a hardened media server. HTTP availability is not a playback guarantee.

No publishing, model calls, source-media edits, recording, or automatic verification claims result from this review feature. Human listening feedback should be recorded separately before classifying detector boundaries as reviewed.


### Playback-delivery correction and the local server

The user reported selection without audible playback. Read-only HTTP checks established that the initial standard Python preview server ignored byte-range requests (returned HTTP 200 instead of a partial HTTP 206 response). This was a concrete compatibility weakness, not proof of every browser failure's cause. We corrected delivery, added MP3 playback, and handled asynchronous seek/user activation explicitly. Only the user can confirm audible playback in their tab; we do not claim that code simulations prove it.

`serve_review.py` provides an explicit loopback-only development server. `ReviewHandler` inherits standard static-file behavior but adds single-range responses for media seeking. `send_head` parses a requested start/end or suffix, checks file-size bounds, seeks the file, and sends correct partial content length/type/range headers. Invalid/multipart ranges receive 416 rather than incorrect data. `copyfile` sends only the selected byte count and tolerates a client disconnect during a range transfer. `end_headers` advertises ranges and avoids stale cached HTML. Normal full requests and HEAD requests still work.

Syntax: `partial(Handler, directory=...)` pre-fills the serving directory argument. `ThreadingHTTPServer` handles concurrent local requests. `re.fullmatch` validates the entire range header; integer conversion follows validation. Byte ranges address encoded file bytes, not speech samples or seconds. `min` bounds reads/end positions; a remaining-byte counter prevents accidentally streaming the rest of a file after a partial response.

The server CLI validates a run folder/port, binds to localhost only, prints the local address, and closes its listener on interruption. It is a development server, not production authentication or a hardened upload service. Serve a run folder, never the project root containing credentials.

Nine tests start a temporary real loopback server and verify normal/suffix/open-ended/clamped ranges, invalid ranges, full media, HEAD behavior, and MIME type. A live check of our MP3 endpoint returned 206 with the requested 44 bytes and `audio/mpeg`. The full source audio, detector JSON, and source video are unchanged. Browser byte-range support helps seeking but does not alone guarantee audible output.

The user explicitly declined Mac recording/inspection permissions. No further browser automation, capture, or tab reloads are used. Page updates are saved on disk; the user refreshes and tests manually. The local server remains available for that authorized review. The earlier attempted browser-selection test did not establish end-to-end playback success and is not counted as a passing browser test.

## Feature 4 — protected groups, lossless ASR windows, and exact-file review

### Why three different timing objects exist

1. **Raw VAD regions** are estimated speech activity. A quiet onset may fall before a raw region; they are not word boundaries.
2. **Protected groups** add automatic safety context around raw regions and merge overlaps. They are useful continuous listening units and can exceed 30 seconds. They must not be sent to Bodhan as one request merely because they are grouped.
3. **ASR windows** are the actual exported WAV files, each <=30 seconds including context. These are future request inputs. Their non-overlapping ownership cores cover the complete source timeline, including VAD gaps, so misclassified quiet speech is not thrown away.

The user found an apparent contradiction when a 33-second protected group appeared beside our claim about <=30-second ASR windows. The claim concerned different artifacts, but the UI was misleading. The main demo review now plays the exact request files. Protected groups and raw VAD remain separate comparison pages.

### `BoundarySettings`

**Syntax:** a frozen dataclass contains integer millisecond settings. `asdict(self)` supports validating each value. Integer division `//` converts milliseconds to whole sample counts at 16 kHz.

**Purpose:** make the safety policy reproducible. Defaults: 500 ms context, 25-second preferred core length, a 30-second maximum request, and 300 ms minimum pause candidate. The preferred length is softer than the hard maximum: a nearby pause may be chosen beyond 25 seconds if the full context still fits. Invalid combinations are rejected, including context that leaves insufficient space for the preferred core.

These are tested starting rules, not proof of perfect speech boundaries. More context can recover a clipped onset but increases overlap and model input duration. Pauses can contain quiet speech; even this policy must not claim VAD certainty.

### `boundary_plan(frames, raw_regions, settings)`

First validate source length and raw sample ranges. Expand each raw interval by the configured context, clamped to source bounds. If expanded intervals touch/overlap, extend the preceding protected group and retain the IDs of all contributing raw regions. This prevents a connected phrase from being presented as several independently chopped speech fragments.

Next, construct ASR windows independently of those group sizes. A cursor marks the start of the next ownership core. Search detected gaps for an interior cut near the preferred length while keeping room for context. At the recording start there is no missing left context to reserve; later windows reserve both sides. If no valid pause exists, cut at the preferred length and mark the cut `forced_with_overlap`. At the source end, finish with the remaining samples.

Each output window includes:

- Full file start/end samples and seconds, including context.
- Core start/end samples and seconds, excluding duplicated context.
- A stable window ID, cut reason, and whether VAD speech intersects its core.
- A flag that later transcript overlap reconciliation is required.

**Why cores?** If adjacent windows share a second of context, concatenating their transcripts blindly can repeat words. Cores define a unique source-ownership partition, but do not give word alignments. Actual text reconciliation remains a future ASR-stage implementation. We deliberately record that requirement rather than pretending the metadata alone solves duplicate words.

**Invariants enforced/tested:** every core advances; adjacent cores meet exactly; the first starts at zero and the last ends at the source end; each core lies inside its file interval; each file is <=30 seconds. No source samples are skipped, including all-silence/no-VAD-detection input. We have not implemented a policy that automatically narrates VAD gaps; later ASR/QA must handle hallucination risks.

**Syntax worth recognizing:** `zip(a, b)` pairs corresponding entries, generator expressions feed `any`/`sum`, `min(candidates, key=...)` chooses the nearest permissible cut, and `while cursor < frames` advances until the whole source is owned. These operations work on measured sample indices, not invented words or LLM timestamps.

### `prepare_boundaries(run, settings)` and CLI

Read the saved detector metadata and matching 16 kHz mono PCM16 source WAV. Refuse to overwrite an existing protected manifest or window folder. Call the planner, seek the WAV reader to each window start, read the exact sample count, verify the byte length, and write a separate PCM WAV. This is lossless sample copying, not a new speech model or media-generation API.

The result is `protected_regions.json` plus `asr_windows/window-0001.wav`, and so on. The manifest contains both protected listening groups and actual ASR windows. Errors are structured; an interrupted export may leave partial files and must not be treated as a completed plan. No automatic overwrite/resume behavior is implied.

```sh
python -m bodhan_dub.boundaries --run outputs/detect-demo
```

The module CLI currently exposes the run and context margin; the Python settings object also contains preferred length, maximum, and pause parameters. This step performs no hosted model calls. A full pipeline integration and transcript stitching are still pending.

### Tests that establish coverage rather than guessed accuracy

Seventeen boundary tests cover invalid settings, expanded-region merging, tiny through 15-minute inputs, no VAD detections, pause preference, and 100 deterministic randomized interval scenarios. A lossless export test writes a nonconstant sample pattern, reads each exported window's core, joins those cores, and requires exact byte-for-byte equality with the original PCM. This catches dropped, reordered, or duplicated ownership samples rather than merely checking a duration field.

None of those assertions establishes that a cut lies between complete words. An overlapping window may contain a partial neighbor word at its outer edge while the complete word is retained in the adjoining window. That is why transcription reconciliation and honest uncertainty reporting remain necessary.

### `window_review.py`: show the actual request, not an approximation

`create_window_review(run)` reads the protected manifest but uses only its ASR window records for the main page. It verifies integer bounds, <=30-second lengths, contiguous core coverage, and source limits. It then opens each numbered WAV and checks its actual frame count/rate/channels/sample width against the record. Display duration is calculated from sample counts. A wrong-sized WAV, over-limit record, incomplete plan, or symlink outside the expected window directory is rejected.

The output is `review_windows.html`, using the packaged `window_review.html` template. Dynamic data is JSON-encoded/HTML-escaped as in the prior reviewer. Generating an existing window review is refused; explicit development updates preserve prior comparison artifacts. For the current demo, `review.html` is a copy of this exact-window page so the existing local URL continues to work.

```sh
python -m bodhan_dub.window_review --run outputs/detect-demo
```

### Exact-window HTML/JavaScript

Each button switches the media element to the corresponding exported WAV file and invokes playback directly in the click handler. There is no asynchronous seek into a long MP3 and no additional preview buffer: the file itself already is the request interval. Native media ending stops playback without a JavaScript stop timer.

The native controls show time within the selected file, starting at zero. A separate **Source time** clock adds the window's original start offset, preserving the original-video reference. This explains why a later window's native scrubber can show zero while source time shows its original position.

- `selectWindow` changes the source file, selected label, core/file times, download link, highlight, and play state. A request counter prevents stale play-Promise callbacks from reporting the wrong selection.
- `timeLabel` formats source times; `clock` adds the current file offset to the selected source start.
- Replay chooses the same actual WAV again. Full playback deliberately selects the complete MP3 and is prominently labeled as not one ASR request.
- End/error handlers report file completion or loading problems. Manual browser decoding/audio-output verification is still needed; no computer recording is requested.

Six Python tests validate page generation, non-overwrite, actual WAV duration, over-limit rejection, complete core coverage, missing metadata, and unsafe file links (some checks are combined in one case). A second Node-only simulation checks that clicking a row really selects the matching WAV path, source-clock mapping, replay, full mode, endings, and errors. It does not prove physical audio output or detect clipped phonemes.

### Current evidence and exact limits

Full fixture-enabled suite: **84 Python cases passed**, plus code-only UI simulations. Demo: 39 raw regions → 12 protected groups → 14 ASR windows. Longest actual request WAV: **29.788 seconds**. The first protected group is **33.004 seconds**, intentionally not one request. No demo cuts required the forced-split fallback in the current plan; continuous-speech tests exercise that path.

All original source samples are retained; all request files satisfy the 30-second limit. These are verified engineering guarantees. Perfect word boundaries, transcript quality, duplicate-word reconciliation, and the final dubbed result are **not** established or implemented by these tests. No paid calls, commits, pushes, or browser recordings occurred for this feature.


## 13. How to explain the media feature in a debrief

You should be able to answer:

1. Why do we keep CLI orchestration separate from reusable media functions?
2. What is a media stream versus a time chunk?
3. Why is a delayed audio track different from simply shorter audio?
4. Why normalize an analysis copy instead of changing the original video?
5. Why inspect the WAV even after ffmpeg succeeds?
6. Why does `prepared` not mean `dubbing_complete`?
7. Which tests use real executables, and what do they not prove?
8. Why are type annotations and pinned packages not sufficient runtime validation/reproducibility guarantees?

## 14. Update checklist for every future feature

After implementation and verification, before reporting that feature complete:

- Add its purpose and place in the execution story.
- Explain each new/changed function, class, CLI option, and configuration entry.
- Add unfamiliar syntax in brief with a concrete example.
- Describe input/output contracts, errors, recovery, and why this design was chosen.
- Explain tests by the behavior they prove; state skips and untested cases.
- Update earlier sections when code changes; do not leave contradictory behavior descriptions.
- Record actual verification evidence without promising unmeasured quality.
- Explain any important third-party dependency boundary, not its entire source code.
- Keep credentials and private data out of examples.

### Feature history

| Feature | Implemented behavior | Walkthrough coverage |
| --- | --- | --- |
| 1. Media preparation | Inspection, aligned audio extraction, preparation CLI/report, local tool setup; 21 passing tests | Sections 1–13 |
| 2. Local speech detection | CPU Silero, validated region/gap timeline, detection CLI; full suite 44 passed | Feature 2 section |
| 3. Local listening review | Clickable regions/gaps, exact-default playback, MP3/byte-range delivery; full suite 60 passed plus Node simulation | Feature 3 section |
| 4. Protected boundaries and ASR windows | Context merging, lossless <=30-second windows, exact-WAV review; full suite 84 passed | Feature 4 section |
| 5. Bodhan transcription client | Offline-tested upload, caching, bounded retries, raw window reports; full suite 122 passed | Feature 5 section |
| 6. Shorter ASR / pacing | Live 26-window rollout; shared request pacing and cooldown handling | Features 6–11 section |
| 7. Reconciliation | Source-backed English reconstruction with portable Groq client | Features 6–11 section |
| 8. Automatic correction | Seven reviewed flags handled; source-evidenced terminology normalization | Features 6–11 section |
| 9. Thought grouping | 47 sentence records to 14 groups, coarse timing explicitly retained | Features 6–11 section |
| 10. Translation | 14 units each in hi/ta/bn, three Bodhan calls | Features 6–11 section |
| 11. Speech generation | 42 validated WAVs; content QA/timeline fitting pending | Features 6–11 section |

## Feature 5 — Bodhan transcription, reuse, and bounded recovery

### Purpose and place in the pipeline

The previous feature produced actual request WAVs with overlapping context. This feature adds the client that can send those files to Bodhan and retain **raw English results per window**. It does not join those results, translate them, or produce a dubbed video. Keeping this boundary explicit prevents repeated words from overlapping audio from accidentally reaching synthesis twice.

The implementation is `src/bodhan_dub/transcription.py`; its behavioral tests are `tests/test_transcription.py`. The feature was developed and tested without live API calls. The real transport is present but has not been validated against Bodhan by this feature's tests.

### Execution story

1. Read the existing boundary manifest and validate **every** request WAV before starting any remote work. A malformed final window must fail before we pay for the earlier ones.
2. Recalculate duration and the estimate from WAV samples. Manifest cost fields are not trusted.
3. In the default dry run, print the estimate and stop. No key file is read, no provider call is made, and no output is written.
4. Only explicit `--execute` enters the hosted path. Read the key from the environment or an explicitly selected environment file; never search parent directories automatically.
5. Hash the WAV and the request identity, acquire its cache lock, and inspect saved state before sending anything.
6. Reuse a complete result; block an uncertain previous request; otherwise upload the WAV with Bodhan's English transcription model.
7. Save each window's result or failure and update a separate transcription-stage report. Preserve usable earlier results when later work fails.

This separate stage command is deliberate: the assignment's full dubbing command is not finished yet. A successful transcription-stage exit does not claim that the complete assignment pipeline works.

### Small configuration and transport types

`RetrySettings` holds total attempts (default three, allowed one to five), the transport timeout (default 60 seconds, at most 300), and a per-client concurrency bound (default one, at most four). `__post_init__` validates these when the object is created, before requests are possible. The run orchestrator currently processes windows sequentially; a concurrency setting is not evidence of an implemented parallel runner.

`Request` carries endpoint, headers, upload bytes, and timeout. `Response` carries HTTP status, bytes, and headers. `field(repr=False)` keeps credentials and audio/response contents out of the automatic dataclass representation. It does not encrypt memory or make arbitrary logging safe. `@dataclass(frozen=True)` prevents reassigning the object's attributes; it does not recursively freeze a dictionary inside it.

`AmbiguousCall` represents a transport outcome where the provider may have processed the audio even though we did not receive a usable answer. That distinction matters because repeating a timeout can pay twice.

`http_transport(request)` uses Python's standard HTTP library. A custom redirect handler refuses redirects so the bearer token is not forwarded to a new location. The response has a two-megabyte size ceiling. HTTP error bodies are discarded because a service could echo private input or credentials. Network errors become ambiguous outcomes. The timeout is the underlying network-operation timeout, not a guaranteed hard wall-clock deadline for an entire streamed response.

### Validate and construct the request

`audio_input(path)` opens the WAV and requires nonempty, uncompressed, 16 kHz, mono, 16-bit audio of at most 30 seconds. It reads the declared samples to catch truncation, then computes a SHA-256 digest of the file bytes. Duration comes from `frames / sample_rate`, avoiding player display rounding. A `Decimal` calculation estimates cost at the assignment/published rate of ₹0.10 per audio minute; it is not an invoice.

`multipart(blob, language)` constructs the HTTP multipart body: model, source language, and audio. A random boundary separates fields. The uploaded filename is always `window.wav`, so an absolute local pathname is not transmitted as the filename. Byte strings (`b'...'`) and `.encode()` are needed because HTTP sends bytes rather than Python text objects.

`atomic_json(path, value)` writes UTF-8 JSON into a temporary file in the destination directory, then uses `os.replace` to install it atomically. Readers should see either the previous complete file or the next complete file, rather than half-written JSON. Its `finally` block removes a leftover temporary file if replacement fails. This is replacement safety, not a promise of survival across every disk/power failure.

### Cache identity, locks, and the request journal

`TranscriptionClient.__init__` accepts the key, cache directory, settings, transport function, and sleep function. The last two are injectable: tests can return controlled responses and record backoff without networking or real delays. The key remains private to the client and request header; it is not part of the cache identity or normal reports.

`transcribe(path, language='en', expected_sha256=None)` currently accepts English source only. It validates the file again and compares its digest with preflight when supplied. A changed file stops the request rather than silently submitting different audio with old timing metadata.

The cache identity includes a schema version, exact file digest, model, and language. Identical audio with identical settings reuses a completed result even in another window; different file bytes produce another identity. API-key rotation does not invalidate a result. `FileLock` serializes access to the same identity across threads/processes. A bounded semaphore separately limits simultaneous requests through one client. Separate clients do not share a global concurrency budget.

| Saved state | Meaning on the next invocation |
| --- | --- |
| `complete` | Reuse the saved text; no new call or estimated new cost. |
| `in_flight` | A process may have stopped after sending; block automatic resubmission. |
| `unknown` | Processing/billing is uncertain; require investigation before deliberate resubmission. |
| `failed` | The last run received HTTP failure responses; an explicit new run can try again. |
| Invalid/unreadable | Stop and ask for inspection rather than treating a broken cache as a miss. |

The journal is written before each upload. An interruption therefore leaves evidence that a request may have been sent. A completed response is saved before the caller receives it. These rules reduce duplicate work, but cannot guarantee exactly-once billing without provider-side idempotency support. A failed entry can be replaced by an explicitly initiated later attempt; this is not a permanent append-only audit history. Cache files contain transcript text and need the same privacy care as other run artifacts.

### Retry and response rules

`_request(...)` builds the request, records each attempt's latency/status, and applies deterministic recovery rules. HTTP 429 and 5xx responses may retry within the configured **total** attempt count. Ordinary backoff is one second then two seconds at the default limit. The current paced transport separately honors numeric and HTTP-date `Retry-After` cooldowns; the exponential retry loop still has a bounded attempt count. See Feature 6 for the five-minute automatic-wait limit. Invalid credentials, permissions, endpoint, input, and other non-retryable responses stop immediately.

A network exception, interrupted request, oversized answer, or unusable successful response must not cause an automatic paid repeat. The cache records uncertainty instead. This conservative choice can require manual recovery, but avoids pretending an unanswered call was definitely free. It also means our default three-attempt policy does not blindly retry every error.

A successful payload must be an object with string `text` and no error marker. Empty text is cached with an explicit `empty_transcript` status, allowing later quality checks to distinguish silence from a request failure. Provider usage is retained only for selected finite numeric counters. Exact occurrences of the API key are redacted from returned text; raw provider errors are never printed. This is targeted credential protection, not a general personal-data scrubber.

The local `failure(...)` helper attaches known attempt metadata and a conservative cost estimate to a `MediaError`. The report can then retain the cost uncertainty even when there is no successful result. Every attempted call is counted as potentially billed; actual provider billing can be lower.

### Keys, preflight, reports, and CLI

`load_key(env_file)` gives an existing `BODHAN_TRANSCRIBE_KEY` environment variable precedence. Only when needed does it read the explicitly supplied file. Its small parser supports a single assignment with optional `export`, quotes, and comments through `shlex`; it is not a complete dotenv interpreter and performs no variable expansion. It never automatically searches for the repository's `.env`. Dry runs do not invoke it.

`inspect_windows(run)` checks the manifest's sample rate, duration, integer bounds, contiguous non-overlap ownership cores, and actual WAV dimensions. It constructs canonical numbered filenames and rejects file resolution outside the expected window directory. It checks all windows before returning their metadata and estimate. This catches mismatched request files before any paid request, although source-word alignment is still outside its contract.

`transcribe_run(run, client)` attaches each result to its original request and ownership-core ranges and writes `transcription_report.json` after progress. Authentication, endpoint, input-change, and uncertain-billing errors stop subsequent requests. Other window failures can be recorded while independent windows continue. Final status is `transcribed_unreconciled` or `partial_failure`. The report always says `overlap_reconciled: false` and `dubbing_complete: false`; it does not replace the earlier preparation report or fabricate the assignment's final merged English transcript.

`main(argv)` parses the stage CLI and maps validation/failure to a nonzero exit. From the recipe directory, the safe default is:

```sh
python -m bodhan_dub.transcription --run outputs/detect-demo
```

`--execute` explicitly enables hosted requests; do not run it without the user's separate live-call approval. `--env-file` selects a credential file for that execution only. `--attempts` and `--call-timeout` configure bounded recovery. Normal CLI output is a short JSON status rather than provider text or a key.

### Syntax worth recognizing

- `Callable[[Request], Response]` describes a function taking a request and returning a response; this enables dependency injection for tests.
- `RetrySettings | None` means either a settings object or no supplied value. `settings or RetrySettings()` chooses defaults when absent.
- `with lock:` and `with self.limit:` acquire and release resources even when a nested operation raises.
- `{**old, 'cache_hit': True}` copies dictionary fields and overrides a selected field. It leaves the persisted result intact while describing this invocation.
- `raise ... from exc` preserves the exception's internal cause. The CLI prints only the sanitized outer message; arbitrary tracebacks could still expose internal details and should not be pasted into public reports.
- `getattr(error, 'attempts', [])` uses attached metadata when available and a safe default otherwise.
- `sum(values, Decimal(0))` keeps cost arithmetic decimal throughout. The displayed long precision is a calculation, not billing precision.
- `if __name__ == '__main__': raise SystemExit(main())` makes module execution return the intended shell exit status.

### Historical evidence at Feature 5 offline completion

At that stage the fixture-enabled full suite passed **122 Python tests**, including **38 new transcription cases**. Fake responses cover uploads, reuse, changed inputs, retry limits/backoff, permanent errors, uncertain transport outcomes, interruption recovery, malformed success payloads, empty text, credential redaction, corrupt cache, same-audio concurrency, environment precedence, all-window preflight, and partial/fatal reporting. Transcription tests include a network guard; the HTTP transport test uses a fake opener, not Bodhan.

The real demo dry run validated 14 WAVs totaling 342.2666875 seconds including overlap: approximately **₹0.57** for a first pass, with **zero hosted calls**. This feature generated no demo transcripts and did not verify the provider's current response shape, actual charging, or recognition quality. The user confirmed Groq Free tier; no Groq paid estimate or Groq request is part of this feature.

The next step at that historical point was live validation and overlap reconciliation; these are now implemented in the later Features 6–11 section, with final acoustic QA still pending. Retained source/core times are not word timestamps. Never concatenate the raw window texts and label that a reconciled transcript.


### Single-window live integration evidence

With explicit user approval, the existing client submitted only `window-0001.wav` (29.788 seconds) with a one-attempt maximum. Bodhan returned HTTP 200 and a nonempty 39-word English transcript in approximately 21.7 seconds. The request was not a cache hit; its successful result is now cached in the normal run cache. Duration-rate estimated cost: ₹0.0496467; no provider usage/billing counters were supplied, so actual billing remains unconfirmed.

The private run artifact is `outputs/detect-demo/transcription_single_validation.json`. The other thirteen windows were not submitted. The returned text ends mid-statement at “BETWEEN NINE”; this alone does not establish whether the request boundary or recognition caused that ending. Adjacent-window comparison and listening are still required. A usable response proves this client/endpoint integration for one file, not overall transcription accuracy, word timestamps, overlap reconciliation, or completed dubbing. No new feature code, commits, pushes, browser control, or recording was performed for this validation.


### Remaining-window live run

The user separately approved the remaining thirteen windows with one maximum attempt each. The run finished with 14 nonempty results: one cached first-window result and thirteen new successful requests, exactly one attempt per new window. The incremental raw report is `outputs/detect-demo/transcription_report.json`, status `transcribed_unreconciled`. New duration-rate estimated cost is ₹0.5207978; together with the initial validation the estimate is ₹0.5704445. Actual provider billing is unconfirmed. The raw results contain 514 whitespace-separated words in total; this count is not an accuracy measure.

Boundary QA remains open: the first result ends mid-statement at “BETWEEN NINE”, while the next starts a new instruction about chapter vocabulary. That comparison does not establish full coverage or identify the cause. Compare the original request audio and adjacent results before claiming that overlap reconciliation alone will recover any missing speech. No further requests or repair attempts were made. Successful HTTP responses and nonempty text establish transport completion only; no reconciled transcript, word alignment, translation, synthesis, or dubbed output exists yet. No code changes, commits, pushes, recordings, or tab control were made in this run.


### Audit of every raw transcript boundary

User requested an investigation of unfinished sentences throughout all fourteen results. No new hosted calls or feature changes were made. This is a manual review of the saved English text plus deterministic audio-file checks; it is **not a listening-based ground-truth review**. No tool available in this run provided direct semantic listening, and no alternative ASR was used.

| Window | Ending / following text | Text-only finding |
| --- | --- | --- |
| 1 | “AN OBTUSE ANGLE IS BETWEEN NINE” → “Today the seven words…” | Clear unfinished numeric definition; no recovery in the next text. Highest-priority unresolved gap. Do not invent the rest of the definition. |
| 2 | “…the point C is not” → “not Write the definition…” | Predicate can end with an understood “collinear”; next text repeats “not”. Candidate overlap duplication, not a demonstrated omission. |
| 3 | “…is a reflex angle Here” → “Here angle AOC…” | Unfinished transition continues explicitly; repeated “Here” needs context-aware reconciliation. |
| 4 | “Two words together” → “complementary angles…” | Connected introduction/list continues next window; keep together semantically, without claiming word timestamps. |
| 5 | “…write both” → “Fifth word adjacent angles…” | Elliptical but plausible complete instruction. Cannot assume a missing object solely from grammar. |
| 6 | “…Write it and draw the figure” → “Sixth word…” | No textual evidence of a cutoff. |
| 7 | “…are a linear pair” → “Write it and draw…” | Definition followed by instruction; no textual evidence of a cutoff. |
| 8 | “…Seventh word vertically opposite angles” → “When two lines cross…” | Term introduction followed by its definition; semantic continuation, not missing text by itself. |
| 9 | “…Write it and draw the figure” → “Now an example…” | No textual evidence of a cutoff. |
| 10 | “…not a linear pair” → worked example answer | Prompt followed by answer; no textual evidence of a cutoff. |
| 11 | “…Write the example with its reasons” → “Your turn…” | No textual evidence of a cutoff. The awkward “An angle COE…” wording is a separate recognition-quality uncertainty, not proof of missing sentence content. |
| 12 | “…write two answers with reasons” → “Adjacent Yes…” | Task followed by answers; no textual evidence of a cutoff. |
| 13 | “…Compare with what you wrote” → “Your notebook now…” | No textual evidence of a cutoff. |
| 14 | “…the seven words each with a figure” → end of video | Text appears to finish a closing sentence; end-of-audio semantic accuracy is not established by text alone. |

All fourteen current request WAVs were compared byte-for-byte at the PCM level with their exact source intervals: every comparison matched. Every file digest also matched the input digest recorded with the corresponding result. Concatenating only their ownership cores reconstructed the entire analysis WAV exactly. Thus there is no observed sample loss, alteration, or changed-file mismatch in export/upload preparation. These checks do **not** prove ideal sentence boundaries or complete recognition; a recognizer can still omit words despite intact input.

The first ownership boundary is at 29.288 seconds, with request overlap from 28.788 to 29.788. Existing VAD estimates put speech near that boundary; VAD is not word alignment. Do not infer that the missing number exists only at the boundary, or that changing padding alone fixes it.

The current [Bodhan speech-to-text reference](https://console.bodhan.ai/api-docs/) documents file/model/language and a text response, with a 30-second audio ceiling. It does not document an ASR output-budget option or truncation indicator. Therefore an undocumented output cap is only a hypothesis; do not add guessed parameters or blame a specific provider limitation.

Proposed next diagnostic, requiring separate paid-call approval: transcribe the first request as two shorter overlapping source slices, compare results with the original, and retain all versions. Approximately 31 seconds of submitted audio would cost about ₹0.052 at the published duration rate, one attempt per slice. This is a diagnostic, not authorization to change the global window policy or silently replace text. If missing source words cannot be established, keep the run flagged for QA rather than generating a textbook completion with the LLM.


### Exact-half diagnostic: omitted text recovered

The user confirmed by listening that Window 1's original transcript omits later speech, then explicitly authorized splitting that window into two equal halves and submitting each once. Created diagnostic WAVs for 0–14.894 s and 14.894–29.788 s, with no overlap, preserving all original PCM samples. Both calls succeeded with one actual attempt each, no cache hits. Combined estimated new cost: ₹0.0496467. Results are saved separately in `outputs/detect-demo/window-0001-halves/diagnostic_report.json`; the original transcript/cache and main review page were preserved.

The first half ends “A RIGHT”; the second starts “angle is exactly ninety degrees” and includes the obtuse-angle range, straight angle, and the chapter introduction ending in “parallel lines”. This recovers content missing from the original full-window response. The two texts were displayed verbatim in chat for user verification. The equal split itself cuts a sentence; it is a diagnostic, not a final sentence-grouping policy.

This controlled example establishes that shorter requests recovered omitted speech for this particular window. It does not establish an exact provider length/token cap, prove that every 15-second input is complete, or validate all other windows. No new ASR parameter, global chunking policy, automatic quality repair, or transcript reconciliation has been implemented. The original cached full-window text is known incomplete and must not be treated as approved final content; retain it as evidence until a discussed quality/recovery policy supersedes it. No additional paid requests, commits, pushes, browser control, or recording occurred.

## Features 6–11 — current implementation through speech generation

This section supersedes earlier stage-status statements. The user temporarily paused walkthrough edits while the correction policy was discussed, then authorized this consolidation. Current evidence: **339 fixture-enabled Python tests passed**, Cookbook recipe validation passed, and **42 real Bodhan speech files** were generated. The assignment is **not finished**: content verification, final alignment, duration fitting, loudness normalization, subtitles, video assembly, and full CLI integration remain pending.

### The current execution story

```text
Original video → aligned analysis WAV → VAD speech/gap evidence
 → short overlapping ASR requests → raw Bodhan English results
 → Groq reconciliation, checked against source words
 → automatic overlap/term correction → connected English thought groups
 → labelled document translation with Bodhan, one request per language
 → Bodhan speech per translated unit → validated WAV files and durations
 → PENDING: back-transcription QA, final timing/fitting, subtitles and muxing
```

The full assignment command is not yet wired through these stages. The reusable modules and stage commands exist; some demo window preparation was performed by temporary development scripts. Those scripts are not installed package entry points. Clean-clone reproduction of the complete new short-window preparation path must be integrated before submission.

### Feature 6: shorter ASR windows and request pacing

The original 29.788-second request returned incomplete text despite HTTP 200. The user confirmed that the audio contained speech omitted by the recognizer. Two exact 14.894-second halves recovered the angle explanation, although the second half still omitted an overlapping “today” captured by its neighbor. This established an omission in the response, not loss in the WAV export. It did not establish a particular server token cap.

The opening-minute experiment used five requests capped at 15 seconds, including two seconds of shared audio. Near-pause cuts were preferred within two seconds of a 13-second core target. Cuts without a nearby pause retained overlap. Every exported PCM interval matched its source, and joining ownership cores reconstructed the source exactly. The full rollout produced **26 windows**. Four results were reused; 22 new identities were submitted. One rate-limited request required a separately approved retry. A preflight-only redundant tiny final window was caught before any requests and corrected by letting the last useful window own the recording's end.

These 15-second exports remain separate from the older 30-second boundary CLI and reviewer. The main historical review page still points at its existing request files; do not assume it was regenerated for the newer rollout. Technical ASR cuts can fall inside sentences. Complete translation thoughts are reconstructed later, and final speech alignment is still unfinished.

`request_pacing.py` provides two reusable classes:

| Code | Purpose and behavior |
|---|---|
| `RequestPacer.__init__` | Validates a configurable request rate; default 10 starts/minute means six seconds between starts. Accepts clock/sleep functions so tests do not wait in real time. |
| `RequestPacer.wait` | Reserves a permitted start under a lock, waits outside the lock, then rechecks. A response arriving during the wait can extend the cooldown. |
| `RequestPacer.observe` | On HTTP 429, respects numeric or HTTP-date `Retry-After`; uses 60 seconds if absent/unusable. Automatic waits over five minutes fail explicitly. |
| `PacedTransport` | Applies the same pacer before transport and feeds responses back into it. Retry attempts and concurrent workers therefore share the start-rate bound. |

Transcription's `paced_http_transport` supplies a shared default pacer for real requests. The transcription CLI exposes `--requests-per-minute`; injected test transports remain independently controllable. Pacing is per process/client, not a provider-wide account lock. Independent processes or other account usage can still cause a rate limit. A cooldown is not permission for an unlimited number of retries; the caller's attempt budget still applies.

**Syntax:** `with self.lock:` protects shared state; sleeping outside that block lets other threads report cooldowns. `time.monotonic()` measures elapsed time without wall-clock changes. HTTP dates require the separate wall clock.

### Feature 7: source-backed reconciliation

`reconciliation.py` lets the configured general-purpose LLM organize English text while keeping speech recognition exclusively with Bodhan. The current live interface is simple: rules in a system message, saved transcripts as JSON in a user message, and connected English text plus issue codes as the response. No audio or target-language writing is sent to this LLM.

The initial word-index proposals proved unnecessarily difficult for the model. The current Python layer infers and validates source references from returned prose. Old compiled-plan helpers remain internal/tested adapters; they are not the current LLM response contract.

| Function/type | What it does and why |
|---|---|
| `quality_check` | Rejects empty, grossly repetitive, implausibly long-token or predominantly wrong-script English recognition before planning. This is a coarse check, not proof of English accuracy. |
| `source_windows` | Validates unique IDs, chronological finite bounds and bounded text/token counts; creates the source word ledger. |
| `fail` | Raises a consistent `MediaError` for rejected plans. |
| `_validate_plan`, `validate_plan` | Check every selected span, source order, punctuation, exclusion support and complete word accounting. The wrapper sanitizes malformed structures. |
| `compile_joins` | Converts edge-trim decisions into a complete source-span plan; only supported neighboring overlaps may justify deletions. A wholly redundant window can contribute no words if all its tokens have justified support. |
| `join_candidates` | Enumerates edge choices already accepted by the provenance validator. It does not consult a dictionary or generate wording. |
| `compile_choices` | Retained adapter for choosing enumerated overlap IDs; the current direct-text LLM path does not use it. |
| `validate_connected_text` | Matches model words to a legal chronological path through the original windows. Dynamic-programming state records how much of the next window is trimmed and how far the returned text has matched. Interior omission, unsupported substitution and reordered words fail. |
| `source_checked_reconstruction` | Handles harmless hyphen separators and can reject up to three model-added grammar words while preserving the entire supported source path. It cannot fill a missing source word. Inserted names, numbers, negations and other vocabulary are not silently repaired. Rejected additions are logged. |
| `LLMSettings`, `load_settings` | Validate provider URL/model/key and output budget, reading the three portable LLM variables. Environment wins; only an explicitly supplied file is read. Key representation is suppressed. |
| `sdk_completion` | Uses the pinned OpenAI-compatible SDK, with no SDK retries, tools, audio, or alternate speech provider. Non-stop/incomplete responses are rejected. Output allowance is configurable. |
| `reconcile` | Hashes input, provider/model, version and output budget; locks/cache-journals a call; revalidates cached plans. Failed proposals stay untrusted and are not silently resubmitted. |
| `read_experiment` | Reads raw window results and refuses to silently omit failed or empty windows. |
| `main` | Defaults to a key-free preflight. `--execute` enables the explicit LLM run; existing output is not overwritten. |

Exact overlap deletion needs a unique multiword anchor. Repeated single words are ambiguous and cannot be deleted just because they match. A local one-word recognition alternative must already occur in the adjacent source phrase. Conflicting literal numbers and single-letter identifiers receive stricter handling. These structural checks cannot prove which recognizer variant matches the actual speaker.

The full-video model response initially exceeded its output budget. The budget was increased; subsequent source checks rejected omissions and additions. The accepted draft was reconstructed from the saved proposal offline, rejecting the unsupported grammar words “and” and “is.” No new speech calls were needed. This is evidence that invalid model prose was filtered, not a claim that the model never attempted an edit.

**Syntax:** a source span uses zero-based, end-exclusive indices: `tokens[2:5]` selects three words. Sets detect reused references. Dictionary state keys such as `(trim_start, output_position)` let dynamic programming reuse partial matching work. `field(repr=False)` suppresses sensitive automatic representations; it is not encryption.

### Feature 8: automatic correction and recovery policy

`corrections.py` turns warnings into unattended decisions. Evaluators are not expected to manually correct transcripts before the command produces outputs. Flags remain in audit data; recoverable disagreements are handled automatically, and unresolved source is marked failed/skipped with reasons.

| Function/type | Purpose |
|---|---|
| `source_plan` | Rebuilds a validator-compatible plan from the saved draft. |
| `word_refs` | Flattens sentence spans into ordered source-word references. |
| `original_joins` | Recovers the draft's existing boundary choices so valid decisions need not be reinvented. |
| `retained_variant` | Locates the actual source word selected by a variant choice. |
| `consistency_score` | Looks for corroborating two/three-word phrases in other non-overlapping source windows. It uses existing Bodhan words, not external knowledge. |
| `render_with_original_punctuation` | Transfers existing punctuation to surviving/corresponding words using `SequenceMatcher`; new wording is never created. Final thought grouping is separate. |
| `correct` | Applies supported duplicate/variant choices, protects configured terms, enforces a shared recovery budget, retains evidence and marks unresolved sentences skipped. |
| `BodhanBoundaryRecovery` | Exports a source-matching interval of at most 15 seconds and calls the Bodhan client once. Validates the returned recognition and saves evidence. It cannot invoke another ASR provider. |
| `main` | Runs cached-source corrections by default. `--allow-bodhan-recovery` explicitly enables potentially billable recovery; `--recovery-budget` allows up to three callbacks, default two when enabled. `--protect` marks supplied names/terms as requiring extra evidence. |

Duplicate decisions require valid neighboring overlap support. Existing complete source wording can resolve “Fifth work/word” or “Seventh one/word.” Repeated technical wording can be normalized only after an observed overlap establishes the competing source spellings and independent source passages corroborate the whole replacement phrase. Every normalization logs old/new tokens and actual supporting source references. Raw recognition is preserved; the derived source ledger records the correction explicitly.

For this run, all **seven reviewed flags** were handled; the scan resolved **20 overlaps overall** and logged **four term normalizations**. “Leanier/leaning pair” became the already-supported “linear pair.” No extra API call was needed. A resolved policy event is not independent acoustic proof that every word is correct. Proper-name detection is not comprehensive: supplied protected terms receive extra checks, and future glossary integration is still needed.

Recovery accepts corroboration only for an existing supported alternative. If missing content cannot be reconstructed safely, the affected source stays blocked. A `possible_omission` flag cannot be cleared merely because nearby duplicate words match. Recovery exhaustion produces machine-readable failures/skips rather than an interactive review requirement.

**Syntax:** `deepcopy` lets candidate joins be tested without changing the accepted plan. `SequenceMatcher(..., autojunk=False)` aligns repeated word sequences without its default common-token shortcut. `Counter`/collections count evidence; evidence counts are not calibrated confidence probabilities.

### Feature 9: connected speech-unit grouping

`grouping.py` groups existing corrected sentences into complete connected thoughts for translation. The LLM chooses only the final sentence ID in each group. It cannot rewrite text, manufacture words, or provide timestamps. This is substantially simpler than asking it to return word indices.

| Function/type | Purpose |
|---|---|
| `GroupSettings` | Sets positive bounds: 60 words, 500 characters, 45 seconds of source-window envelope, and a one-second long-pause threshold. |
| `grouping_input` | Revalidates text against source spans and assigns small ordered sentence IDs. |
| `exceeds` | Checks a proposed group's size. A single existing oversized sentence is preserved and flagged rather than chopped silently. |
| `fallback_plan` | Uses existing punctuation, short introductions/connectors and bounds when the LLM is unavailable. Ready and skipped source are kept separate. |
| `validate_groups` | Requires increasing ends covering every sentence exactly once; rejects text rewriting, missing coverage and invalid issue references. |
| `_llm_plan`, `llm_plan` | Cache and lock grouping decisions. Invalid/unavailable planning falls back deterministically with a recorded reason, without repeated calls. |
| `build_groups` | Joins source text unchanged, attaches source spans, retains VAD speech/gap evidence and checks exact ordered word preservation. |
| `main` | Preflights source/timeline before key access; writes deterministic groups unless `--use-llm` is selected. |

The live result is **47 corrected sentence records → 14 groups**, using one Groq call. Word coverage and order passed. The bounds are explicitly **coarse source-window envelopes**; groups may overlap in those bounds. VAD intervals within an envelope are not alignment of that group's specific words. `ready_for_synthesis` and `timeline_ready_for_synthesis` remain false. These groups are ready for translation, not accurate subtitle/playback slots. Final timing work must preserve protected non-speech gaps and report actual sync performance.

**Syntax:** a plan such as `{"ends":[2,5,8]}` means groups 0–2, 3–5 and 6–8. IDs refer to existing sentences, so assembly can preserve all words mechanically. This is a plan, not generated prose.

### Feature 10: Bodhan translation with full-document context

`translation.py` sends all labelled English groups as one document per language. This gives the translation model full terminology context while avoiding 42 independent calls. Each `<segment id="...">` label maps the returned text back to its original group. Labels are structural metadata and are never sent to speech synthesis as spoken text.

The working hosted route is `/v1/chat/completions`, model `indic-translate`, using exactly one user message with the vendor's target-language-only prompt. No general-purpose LLM writes or rewrites the target text. The documented `/translate` route returned 404 in the earlier probe; the successful chat route was used here. Generation settings are not guessed; the requested output allowance uses the documented `max_output_tokens` field.

| Function/type | Purpose |
|---|---|
| `TranslationSettings` | Validates attempts, output-token allowance, timeout and request pacing. Runtime clients permit up to three transient attempts; the demonstrated live run selected one. |
| `load_key`, `languages` | Load only the translation key from environment/explicit file and validate distinct supported target codes. No automatic dotenv search. |
| `inspect_units` | Preflights all IDs, statuses, text sizes and finite source bounds before any paid request. |
| `document` | Escapes text into the labelled XML document so source math symbols cannot break its structure. |
| `parse_document` | Requires the expected root/labels, rejects unlabelled/nested/unknown content, and identifies missing/duplicate/empty groups. XML entity/DOCTYPE payloads are refused. |
| `digit_groups` | Normalizes Unicode digits for literal-number comparison; it does not prove translated number-word meaning. |
| `translation_quality` | Rejects empty/degenerate output, English echo, missing expected script and literal-digit mismatch. These are sanity checks, not native-speaker accuracy validation. |
| `TranslationClient.translate` | Locks the document context, reuses valid unit results and submits only missing units on an explicit resumed run. Context changes invalidate reuse conservatively. |
| `TranslationClient._request` | Journals before sending, uses shared pacing, bounds transient retries, rejects truncated/malformed successful responses and blocks uncertain paid repeats. |
| `translate_run` | Isolates language failures, writes incremental language/run reports and preserves successes. Shared credential/endpoint errors stop unnecessary calls. |
| `main` | Defaults to an estimate with no keys, writes or hosted calls; `--execute` explicitly enables the selected live stage. |

HTTP success is insufficient. The finish reason and any explicit truncation flag are checked, as are labels and target script. Valid units survive partial document failure in the cache. Actual provider usage is kept separately from a conservative requested-token bound; failures can carry uncertain charges. Explicit glossary enforcement and full mathematical/number-word semantic validation remain unfinished.

Live evidence: **3 successful calls**, **14 units each** in Hindi/Tamil/Bengali; output tokens 1,033 / 1,083 / 1,075. Published-rate usage estimate: **₹0.06382**. English source characters: **2,825**; translations: Hindi **2,845**, Tamil **3,408**, Bengali **2,892**, combined **9,145**. Counts include spaces/punctuation and use Python Unicode string length, not audio duration or bytes.

**Syntax:** `html.escape` protects document structure, while `ElementTree` parses returned labels. `Decimal` keeps rate arithmetic predictable. A cache context hash includes the full labelled source and target; it excludes the key.

### Feature 11: speech generation and audio evidence

`synthesis.py` uses Bodhan `indic-speak` only. Each translated group becomes one speech request. Voices remain consistent per language: Hindi **Kavya**, Tamil **Anitha**, Bengali **Ishita**. Overrides use documented voice names; diarization and original-speaker cloning are not implemented. Neutral delivery is used; no style was invented or required.

| Function/type | Purpose |
|---|---|
| `SpeechSettings` | Validates attempt/worker bounds, network timeout, request rate and maximum returned-audio duration. |
| `load_key` | Reads only the speech key, honoring environment precedence and explicit environment-file selection. |
| `speech_transport` | Sends the fixed Bodhan speech request without redirects, bounds binary responses at 32 MB, and omits HTTP error bodies from reports. |
| `inspect_wav` | Requires complete, nonempty PCM16 mono 24 kHz data; measures real sample duration, peak/RMS and digest, and rejects wholly silent audio. RMS dBFS is not LUFS. |
| `normalize_audio` | Sniffs bytes instead of trusting MIME. Native conforming WAV is retained; recognizable WAV/MP3 needing conversion is decoded locally with ffmpeg. This is media processing, not another speech model. |
| `atomic_bytes` | Writes audio to a temporary file and replaces the destination atomically. |
| `SpeechClient.speak` | Caches by text/language/voice/model, locks duplicate requests and validates cached audio/hash before reuse. |
| `SpeechClient._request` | Records the request before sending, preserves raw response evidence, validates/normalizes audio, and attaches character-rate cost estimates. Unknown or unusable calls are not automatically paid twice. |
| `inspect_translations` | Preflights text/voice/IDs and retains failures from one language as failed jobs rather than preventing other languages from generating. |
| `synthesize_run` | Uses a bounded thread pool plus one shared pacer/semaphore; writes progress as futures complete, preserves successes and refuses conflicting output overwrites. |
| `main` | Prints a zero-call estimate by default. Live mode preflights ffmpeg, then uses explicitly selected credentials/output. |

The actual JSON request carries model, translated `input`, voice and an `instructions` **JSON string** containing `lang`. A nested dictionary would be a different request contract. The code is prepared for mislabeled audio because earlier real probes returned WAV bytes labelled `audio/mpeg`.

All **42 audio files** generated successfully, one attempt per unit, with two workers and ten request starts/minute. The current speech pass contains **9,145 characters**, estimated at **₹5.4870**. Format/duration validation passed; no content verification or timeline fit is implied.

| Language | Files | Total generated speech | Longest unit |
|---|---:|---:|---:|
| Hindi | 14 | 248.235 s | 27.136 s |
| Tamil | 14 | 295.765 s | 30.549 s |
| Bengali | 14 | 290.645 s | 28.843 s |

The Tamil unit over 30 seconds must be split appropriately before any ASR verification request; the ASR limit still applies. None of these speech totals is a final video-track duration. No video shortening, pause filling or clipping has been performed. Loudness is measured only as peak/RMS at this stage; −16 LUFS normalization is pending.

**Syntax:** `ThreadPoolExecutor(max_workers=2)` limits concurrent work; `as_completed` yields finished results without assuming completion order. The parent writes progress reports, then restores unit order at the end. `array('h', pcm)` reads signed 16-bit samples; big-endian machines require byte swapping. `frames / sample_rate` gives duration from the actual waveform rather than guessing from character count.

### Tests and practical limits

Latest complete run: **339 passed**, no skips with the permitted positive speech fixture and local media tools. The tests include real container extraction, packaged local VAD, exact source reconstruction, network-blocked model fixtures, cache/concurrency/interruption tests and actual MP3-to-WAV decoding with ffmpeg. They do not establish native-language naturalness or final sync.

| Test module added after the original media/review work | Cases |
|---|---:|
| `test_transcription.py` | 38 |
| `test_request_pacing.py` | 19 |
| `test_reconciliation.py` | 78 |
| `test_corrections.py` | 19 |
| `test_grouping.py` | 27 |
| `test_translation.py` | 40 |
| `test_synthesis.py` | 34 |

The earlier media/VAD/review/boundary modules contribute the remaining 84 cases. Cookbook `scripts/validate_recipe.py examples/video_dubbing` passed. Root `make check`, clean-container reproduction and the required complete 30-second dubbing smoke test are still pending.

The pinned OpenAI-compatible SDK is `openai==2.8.1`; its tested dependency versions are recorded in requirements. It is used for the general-purpose English planner only. Bodhan translation and speech use fixed Bodhan HTTP endpoints. No additional ASR, translation or TTS provider was introduced.

### Cost report and credit guardrail

The saved `cost_report.json` counts unique completed ASR-cache requests, current translation token usage, generated-speech character counts and earlier probe reports. It avoids adding historical snapshot reports twice. It includes an allowance for the earlier HTTP 429 attempt if that was billed; actual billing for that failure is unconfirmed. One old 404 probe has no usage and is explicitly listed as unmetered, not asserted free.

| Category, including earlier recorded experiments | Estimate |
|---|---:|
| ASR plus possible rate-limited-request charge | ₹1.32 |
| Translation | ₹0.06 |
| Speech generation | ₹5.52 |
| Groq, user-confirmed Free tier | ₹0.00 |
| Conservative tracked total | **₹6.90** |
| Remaining from the user's ₹10 allowance | **about ₹3.10** |

Exact recorded successful-call estimates total ₹6.87433785; adding the uncertain ₹0.02256 rate-limit attempt gives ₹6.89689785. These are published-rate calculations, not a dashboard-confirmed account balance. External usage, unrecorded calls and provider metering conventions can change actual credit.

The prospective back-transcription pass has **not run**. With <=15-second verification requests and overlap, the estimate is **83 calls / 916.645 submitted audio seconds / ₹1.52774**, leaving roughly **₹1.57536** from the tracked allowance. Future paid work must be estimated and approved within the remaining credit; avoid regenerating valid cached audio. There is no account-wide hard-credit reservation service implemented yet.

### Current artifacts and commands

The current run is under ignored `outputs/detect-demo/full-video-15s/`:

- `experiment_report.json`: 26 shorter ASR windows, cached results and the explicitly retried 429.
- `reconciliation.json`, `transcript.en.draft.txt`: original source-checked draft and seven audit flags.
- `corrections.final.json`, `transcript.en.corrected.txt`: automatic policy outcomes, 20 overlap resolutions and four source-backed term normalizations; original artifacts remain preserved.
- `speech_units.en.json`: fourteen connected groups with coarse source evidence.
- `translations/translation_report.json`, `translation.<lang>.json`, `translated.<lang>.txt`: provider translations, mappings and readable review copies.
- `synthesis/synthesis_report.json`, `synthesis/<lang>/unit-*.wav`: 42 generated clips and measured metadata.
- `cost_report.json`: budget estimates and their uncertainty.

Safe estimation examples, from the recipe directory:

```sh
python -m bodhan_dub.translation \
  --units outputs/detect-demo/full-video-15s/speech_units.en.json
python -m bodhan_dub.synthesis \
  --translations outputs/detect-demo/full-video-15s/translations/translation_report.json
```

These two commands do not load keys or make calls without `--execute`. Reconciliation uses `--execute`; grouping uses `--use-llm`; correction's hosted recovery uses `--allow-bodhan-recovery`. Never infer authorization for new paid calls from an example command. Existing result names generally refuse overwrite, while designated run reports/cache journals are progress snapshots. Some journals retain only the most recent deliberate retry; this is not a complete append-only billing ledger.

### What remains before submission

1. Content QA: Bodhan-only back-transcription and per-unit CER with documented normalization; bounded recovery/skip reasons.
2. Final source alignment and actual non-overlapping playback slots. Current window envelopes do not meet the sync target by themselves.
3. Fit generated audio, protect non-speech gaps, enforce conservative speed limits, and apply last-resort English condensation only where necessary.
4. Loudness normalization, subtitle timing/readability, audio assembly and video muxing/duration checks.
5. Integrate all stages under the assignment CLI, including glossary/voice schemas, language support and partial-language exit behavior. Promote temporary short-window preparation into reproducible package code.
6. Full 30-second smoke test, hi/ta/bn dubbed demos, clean clone/container setup, final reports/diagrams and submission materials.

The user asked to finish by **tomorrow (November 21, IST)**; the earlier Sunday target is superseded for planning. UI/OCR bonuses, speaker-aware voices and advanced separation remain deferred. No assistant commits, pushes, PR publication or submission messages were performed. The user forbids browser recording/control; manual listening remains under their control. Work is paused after this documentation update, before additional paid QA or alignment work.


## Feature 12 — independent speech-content verification

### Purpose and flow

`verification.py` checks what Bodhan recognizes in each generated speech clip against the exact translated text supplied to synthesis. A valid WAV and the expected duration do not establish that all intended words were spoken. This additional stage produces a per-unit character error rate (CER), preserves the raw recognition evidence, and identifies units requiring recovery. It does not change translations, regenerate speech, claim naturalness, or fit audio onto the video timeline.

The verification reference stays local. Bodhan receives audio and its language code, without the expected sentence as a hint. This avoids encouraging the verifier to reproduce the reference regardless of the recording. No Groq call or other speech provider participates in this check.

```text
synthesis report + unchanged 24 kHz generated WAV
  → inspect synthesis identity and reference text
  → convert to 16 kHz mono PCM16 analysis audio
  → export requests of at most 15 seconds with shared audio context
  → Bodhan ASR once per request, paced and cached
  → conservatively merge overlapping native-language recognition
  → normalize reference/hypothesis and calculate CER
  → per-unit status, raw evidence, costs and limitations in JSON
```

### Functions and responsibilities

| Function | Why it exists |
|---|---|
| `inspect_synthesis()` | Connects a unit's translated reference to its generated file; validates unit IDs, supported languages, WAV properties and the recorded SHA-256. A changed file cannot silently reuse old evidence. |
| `estimate()` | Counts actual bounded requests, including duplicated overlap audio, before loading keys or spending. Duration is based on the resampled sample count, rather than translated character count. |
| `window_ranges()` | Divides long generated clips into at-most-15-second requests, normally sharing two seconds around internal cuts. Separate contiguous ownership cores reconstruct the analysis audio without duplicated or dropped samples. |
| `prepare()` | Resamples locally with FFmpeg, checks duration and exact exported PCM, saves source/analysis identities, and refuses incompatible existing files. Conversion changes sample rate; it does not change playback speed. |
| `merge_chunks()` | Removes matching suffix/prefix words only across overlapping request ranges. It does not use the reference to fill missing words. Weak or unresolved matches stay reported; conservative retention may increase CER. |
| `normalize()` | Applies Unicode NFKC, case folding and decimal-digit normalization, retaining letters, combining marks and numbers while removing spaces/punctuation. Retaining combining marks matters for Indian scripts. |
| `character_error_rate()` | Computes Levenshtein edit distance divided by normalized reference length. It measures character differences, not semantic equivalence. Values may exceed one when there are many insertions. |
| `assess()` | Produces a metric only when every request for a unit succeeded. Empty/degenerate recognition and excessive differences are marked for recovery; incomplete units get a reason rather than a misleading partial metric. |
| `verify()` | Enforces the explicit spending ceiling, uses the shared paced client, saves incremental progress, isolates failures and preserves single-attempt behavior on resume. Shared credential/credit/endpoint failures stop queued new calls. |
| `main()` | Defaults to an offline estimate. Live execution requires the execute flag, explicit output and budget, then constructs the native-language Bodhan client with one attempt per request. |

`transcription.py` now accepts an explicit allowed-language set. Its default remains English-only for source transcription. Verification opts into the supported native-language codes; this does not permit switching speech providers.

### Syntax, briefly

- `Decimal(...)` keeps rupee calculations precise without binary floating-point rounding at the budget boundary.
- `unicodedata.category(...)` classifies Unicode characters; the mark category retains vowel signs and other combining marks.
- The two lists called `previous` and `current` are consecutive edit-distance rows. Reusing rows avoids keeping an entire character-by-character matrix in memory.
- `ThreadPoolExecutor` bounds concurrent workers; `as_completed(...)` saves each result when it finishes. A shared pacer still controls request start frequency across workers.
- A `threading.Event` shares a fatal-provider-failure signal across workers. Already-started calls may finish; queued work sees the signal before another call.
- `raise MediaError(...)` gives expected failures stable machine-readable reasons. The command's nonzero exit status indicates unresolved content checks, even when the HTTP requests themselves succeeded.

### Limits and acceptance policy

The default CER threshold of 0.20 is our configurable development heuristic, **not an assignment-mandated pass threshold**. A low CER does not prove meaning, correct pronunciation, names/numbers, or natural speech. A high CER may come from synthesis, recognition mistakes, or conservative overlap merging; it is not automatically a synthesis defect. Number words and numeric digits can still differ after normalization. Latin diagram labels such as AOC and English technical terms can be recognized phonetically in an Indian script and count as differences despite similar pronunciation. This version does not guess transliteration equivalence or use the reference to disguise those differences.

Exact multiword overlap anchors are preferred. A single long matching word is accepted with a warning. Unmatched overlaps retain both pieces and a warning instead of guessing or asking an LLM to rewrite target-language text. Natural repetitions and ambiguous overlaps remain a limitation without word timestamps.

This approved pass permits one attempt per request and no automatic paid repair. Successful content-cache entries can be reused for free; prior failed requests remain failed on ordinary resume. Further retranscription or resynthesis needs a separately reviewed budget. No final video, subtitles, loudness normalization or source alignment is created by this feature.

### Validation evidence

The full fixture-enabled suite passed **366 tests**, including 27 new verification cases. These cover Unicode marks/digits, insertion/deletion/substitution CER, bounded windows and ownership, reference-independent merging, default/explicit ASR languages, real FFmpeg conversion, source identity, budget rejection, partial-language failures, failed-request resume, credential failure and dry-run behavior. The Cookbook recipe validator also passed.



### Live run evidence

The one-attempt live verification pass finished: 83/83 ASR requests returned text. Across 42 units: 24 pass the provisional CER check, 18 need recovery/review and 0 have incomplete verification. Estimated new cost ₹1.52774; conservative cumulative tracked estimate ₹8.42464, leaving about ₹1.57536 of ₹10. These are published-rate estimates, not a dashboard balance. No paid repair, resynthesis or further translation was run.

| Language | Pass | Needs recovery | Incomplete | Mean unit CER |
|---|---:|---:|---:|---:|
| hi | 12 | 2 | 0 | 0.116 |
| ta | 4 | 10 | 0 | 0.275 |
| bn | 8 | 6 | 0 | 0.184 |

The mean is an unweighted mean of complete unit CER values, not an acoustic accuracy score. Raw requests and per-unit references/hypotheses are in `verification/verification_report.json`. Analysis audio and exact bounded request WAVs are retained under `verification/audio/`. `cost_report.after_verification.json` updates the prior cost snapshot without overwriting it. The earlier synthesis report remains historical; join QA by language/unit ID and source identity.


## Feature 13 — bounded short-piece speech repair

### Purpose and implementation

`speech_repair.py` addresses suspected incomplete speech generation without rewriting translated content or losing the original evidence. Selected unit IDs are partitioned into smaller input pieces, synthesized once each with the original language/voice, then assembled into separate candidate WAVs. Original synthesis and QA artifacts remain unchanged. New candidates are not automatically promoted or declared verified.

| Function | Purpose |
|---|---|
| `split_text()` | Prefer sentence endings, then clause punctuation, then whitespace within a configurable bound. Every original character is retained in order; an unbreakable long word is rejected rather than silently cut. |
| `plan()` | Validate the selected original files and their hashes, retain the original voice and coarse source envelope, assign deterministic child IDs, and calculate exact character-rate synthesis cost. |
| `assemble()` | Require every child to succeed, validate its audio hash, concatenate all 24 kHz mono PCM samples in order without trimming or overlap, and write a separate candidate report. Missing children produce a failed parent instead of incomplete successful audio. |
| `main()` | Default to a free dry run. Explicit execution enforces the synthesis budget, stores the immutable repair plan, uses the existing paced/cached Bodhan client with one attempt per piece, and refuses changed output. |

Syntax, briefly: regular-expression `finditer()` supplies possible boundary positions; slicing partitions the existing string. `enumerate()` assigns stable piece numbers. `''.join(pieces) == text` proves exact text reconstruction. `wave.open()` writes a fresh WAV header around the concatenated PCM; local assembly has no model charge. `Decimal` preserves the exact character-rate budget.

The 140-character bound is a conservative experiment setting, not an official provider limit or a guarantee of complete speech. Splitting can affect phrasing/prosody; naturalness and final timing still require checks. No target-language wording is created by Groq or Python.

### Evidence and result

- **380 fixture-enabled tests passed**, including 14 new repair tests: exact text reconstruction, boundary preference, invalid/unsplittable inputs, selected IDs/voice/cost, complete PCM assembly, missing children, changed text/audio, dry-run and budget rejection.
- Clips 6 and 10 had identical original durations of 30.549333 seconds. Raw provider-response bytes exactly matched the exported WAVs, and verification cores covered all returned audio. The identical endpoint suggests a possible generation limit but does not prove an official cap.
- Seven smaller requests preserved all 667 characters, maximum 138 per piece. Each generated piece is under 14 seconds. Original clip 6 became a separate 26.709333-second candidate; clip 10 became a 36.864-second candidate.
- Seven native-language ASR checks returned text, each once. Two pieces meet the provisional CER threshold and five remain flagged, partly because Latin terms/labels are recognized in Tamil script. No overlap merging is needed for these individual short pieces.
- Clip 10's previously absent final explanation is now recognized. Clip 6 reaches the drawing instruction, but terminology is still garbled in recognition and the writing instruction remains uncertain. Neither parent is marked fully content-verified. No listening-based claim is made.
- New synthesis cost estimate: ₹0.4002; verification: ₹0.10596; combined: **₹0.50616**. Conservative tracked total: **₹8.93080**, remaining **about ₹1.06920** of ₹10. These are estimates, not a dashboard balance.

Artifacts under `outputs/detect-demo/full-video-15s/tamil-short-repair/`: `repair_plan.json`, `parts/synthesis_report.json`, `assembled/synthesis_report.json`, `verification/verification_report.json`, and `repair_quality_report.json`. The latest budget snapshot is `cost_report.after_tamil_repair.json` in the parent run directory.

Remaining: resolve mixed-script terminology/QA uncertainty, assess candidate naturalness, establish accurate source slots, fit/normalize audio, create subtitles, mux video, and integrate/validate the complete assignment CLI. No further paid calls are authorized by this report; no commits, pushes, browser control or recordings were performed.


## Feature 14 — one-passage native-script terminology experiment

### Purpose

`terminology_probe.py` tests whether a Bodhan-produced Tamil-script term is handled more consistently than the English phrase embedded in Tamil. This is a bounded diagnostic, not a complete glossary feature or a replacement for all translated clips. The original translations, both repair candidates and earlier quality reports remain untouched.

The source is the saved opening piece of Tamil clip 6. Indic-Translate translates the English term `adjacent angles`. Python substitutes only that exact selected span with the provider's returned text; every surrounding character stays unchanged. Indic-Speak generates a short sample with the same Anitha voice. Indic-Transcribe then recognizes the sample without seeing its expected text. No general LLM writes Tamil or handles audio.

### Code and syntax

| Function | Purpose |
|---|---|
| `substitute_term()` | Require one exact source occurrence and a bounded Tamil-script provider response, reject retained Latin text, and preserve the rest of the sentence. Reject long output instead of silently truncating it. |
| `run()` | Bind the experiment to the saved source passage, journal the translation and substitution provenance, enforce a small spending ceiling, reuse the existing single-attempt clients, measure generated duration, and check the remaining QA allowance before sending audio. |
| `main()` | Make live execution explicit. A command without `--execute` reports the experiment scope without loading keys, reading the source plan, creating output or making requests. |

Syntax, briefly: `source.count(original) == 1` rejects ambiguous substitutions; `replace(..., 1)` changes just one span. Regular expressions detect leftover Latin letters. Dictionary expansion keeps recorded source metadata while assigning a distinct sample ID and provider-generated text. `Decimal` sums translation, synthesis and verification charges against the explicit ceiling. Stored hashes identify source text; cache/journal behavior comes from the existing stage clients.

The current experiment is deliberately fixed to one term and one saved passage. It does not establish general technical-term equivalence, consistent diagram-label pronunciation, automatic glossary selection or a production fallback. Isolated term translation also needs review in its mathematical context before broad application.

### Evidence

- **391 fixture-enabled tests passed**, including 11 probe cases for exact surrounding-text preservation, missing/duplicate spans, unusable/oversized term outputs, short-input bounds, dry-run behavior and budget rejection.
- Exactly one Indic-Translate, one Indic-Speak and one Indic-Transcribe request succeeded, one attempt each. Explicit experiment ceiling: ₹0.15.
- Provider term: `அருகிலுள்ள கோணங்கள்`. The 62-character candidate generated 4.864 seconds of speech.
- Normalized reference and independent back-transcription matched: **CER 0.0**. Punctuation/spacing are excluded by the documented metric. This establishes recognition agreement for this sample, not proof of naturalness or mathematically ideal terminology. No listening-based quality claim is made.
- New cost estimate: translation ₹0.00064, speech ₹0.0372, verification ₹0.00811; total **₹0.04595**. Conservative tracked total **₹8.97674**, remaining **about ₹1.02326** of ₹10, not a dashboard-confirmed balance.

Artifacts: `tamil-terminology-probe/term_translation.json`, `substitution.json`, `synthesis/ta/terminology-sample.wav`, `verification/verification_report.json` and `probe_report.json`. Latest budget: `cost_report.after_terminology_probe.json`. All paths are under the current ignored run directory.

Broader use requires a discussed terminology/glossary policy and its cost. No remaining flagged clips were automatically regenerated, no final media output was promoted, and no commits/pushes or browser control occurred.


### One-sentence Tamil calculation retry

The user approved one additional Indic-Translate request for the saved English sentence about 360 minus 120 equalling 240. The source says “three sixty minus one twenty which is two forty.” The original full-context Tamil translation incorrectly said 420. The isolated retry instead rendered the English number shorthand literally in Tamil (“three sixty”, “one twenty”, “two forty”), rather than unambiguously writing the full hundreds. It is not accepted as a verified correction and no audio was regenerated. Raw output and usage are saved in `tamil-calculation-retry/retry_report.json`. One attempt, 58 output tokens, estimated new cost ₹0.00116. Latest budget snapshot: `cost_report.after_calculation_retry.json`, approximately ₹1.02 tracked credit remaining. This is an experiment using the existing translation client, not a new implementation feature; test count remains 391.

## Feature 15 — Estimated timeline, loudness, subtitles and video output

**Files:** `timing.py`, `audio_output.py`, `subtitles.py`, `render.py`, `tests/test_output.py`.

**Purpose:** these turn the saved provider results into playable output. Earlier stages knew which English source windows supplied each word but did not have word timestamps. `source_timeline()` distributes source-token positions across observed VAD speech, resolves adjacent bounds and snaps suitable boundaries to long pauses. The output labels this as estimated timing. It does not claim forced alignment or prove the assignment's ±300 ms target.

`audio_output.py` works with mono PCM arrays. A WAV becomes a NumPy `int16` array, whose positions are samples, not words. Capacity is computed as `round(end * rate) - round(start * rate)`, avoiding mismatched rounding at neighbouring boundaries. We retain every fitted sample exactly once. A clip that cannot fit at at most 1.2× is rejected; no samples are cut from the narration to make a misleading success.

`process_clip()` first retains a short safety context while trimming only near-digital-silence edges. It uses FFmpeg tempo processing and two-pass loudness normalization. The measured result is checked again because FFmpeg's sample rounding can differ from the theoretical ratio. `place_pcm()` assigns the resulting samples to allowed intervals. It prefers quiet cut points when it has to bridge a source pause; if no quiet point exists, it records a phrasing warning. That is why physical fit and natural speech are separate claims.

`subtitles.py` preserves Unicode text, wraps cues to at most two lines, and distributes cues across the actual placed speech intervals. Captions do not cover protected gaps. SRT/VTT formatting validates rounded millisecond boundaries and rejects overlap. Captions are phrase estimates, not karaoke word timing.

`render_saved()` reads saved translations, synthesis and QA. With a validated fit manifest it checks hashes, uses the approved normalized audio, assembles the full-duration track, writes subtitles and copies the original video stream while replacing audio. The original video duration is preserved. It writes per-segment events and keeps quality warnings. Original background is retained in long non-speech gaps; no audio separation model is present.

**Syntax, briefly:** `array[a:b]` selects samples from `a` inclusive to `b` exclusive. `np.concatenate(parts)` joins arrays. `**dictionary` copies fields into a new dictionary. `Path / 'name'` constructs a path without manually joining separators. `try/except` isolates one language's failure so other languages can finish.

**Evidence:** the demo renders all twelve connected groups in each of Hindi, Tamil and Bengali, with zero skipped groups. All videos measure 329.267 seconds. Narration loudness measures −16.14, −16.12 and −16.31 LUFS. These are measured media results, not evidence of semantic correctness or naturalness.

## Feature 16 — Connected fit groups and bounded duration recovery

**Files:** `fit_grouping.py`, `fit_recovery.py`, `fit_resume.py`, `fit_validation.py`, `fit_qa.py` and their tests.

**Purpose:** the first draft renderer skipped oversized clips. The user rejected that outcome because it would remove narration. Recovery now starts with the original words and measured generated audio, tries local adjustments, and asks Bodhan for new speech only when necessary. Fourteen original groups become twelve connected groups through generic continuation rules. The merge keeps every English word in order; existing target text and PCM can be concatenated without introducing target-language words.

`compact_quiet()` removes only runs near digital silence and retains a minimum pause. It records the removed ranges. `borrowed_timing()` bounds how much neighbouring unused time a group can use, prevents two groups claiming the same gap, and preserves at least one second in protected long pauses. Neither operation changes the video duration.

If local processing still fails, `english_proposals()` sends only English to the configured LLM. It protects number phrases, labels, negation and configured source glossary terms. Concise parallel phrasing is assembled using existing English labels/categories; a separate LLM judgment checks whether the candidate preserves the teaching facts. This is a safeguard, not proof. Bodhan then translates the accepted English and generates short speech pieces. The actual audio is measured again. A candidate is accepted only when it satisfies the physical fit checks.

`Budget` uses `Decimal`, avoiding binary floating-point surprises in rupee arithmetic. `reserve()` writes a conservative allowance before a provider call. `settle()` replaces it with the known estimate afterwards. If a request's outcome is unknown, the reservation stays. Continuations subtract all earlier recovery spending from the original ceiling; they do not reset the budget.

`fit_resume.py` reuses successful raw provider audio, rather than stretching already stretched audio again. It retries only documented temporary HTTP failures and leaves unknown/timeouts blocked. It preserves old artifacts and writes a fresh continuation. `fit_qa.py` fills any missing back-transcription evidence after a successful retry, so recovered audio cannot silently bypass the content check.

`validate_fit_manifest()` verifies every source group appears once per language, every normalized sample is placed once, audio hashes match, speed stays within the assignment limits, placements stay in bounds, speech does not overlap and protected pause cores remain free. It deliberately returns `source_sync_within_300ms_verified: false` because estimated source timing cannot establish that guarantee.

**Syntax, briefly:** `@property` exposes computed `budget.spent` like an attribute. `Decimal('0.10')` creates an exact decimal amount. `deepcopy()` preserves independent nested dictionaries. A dictionary comprehension such as `{u['id']: u for u in rows}` builds an ID lookup. `all(...)` requires every invariant; it does not prove facts that were not checked.

**Evidence:** all thirty-six final language/group cases pass strict physical validation. Recovery and its final missing-content check cost approximately ₹2.70, within the authorized combined ₹10 ceiling. No native text was written by the LLM. Translation/naturalness concerns remain visible for the next quality pass.

## Feature 17 — Required single command and configuration

**Files:** `__main__.py`, `pipeline.py`, `configuration.py`, translation policy additions in `translation.py`, `scripts/setup.sh`, `Dockerfile` and integration tests.

**Purpose:** previously the recipe exposed separate development commands and the main command stopped after preparation. `python -m bodhan_dub --input ... --langs ... --out ...` now connects the stages. Explicit preparation and detection modes remain available without API credentials. Normal execution validates input, languages and all credentials before paid requests. It saves every stage so failures can be investigated.

`run_pipeline()` builds bounded ASR windows, transcribes them with Bodhan, reconciles source words, groups connected English, translates and synthesizes each language, back-transcribes speech, runs bounded fitting recovery and renders complete languages. Failed languages get reasons and a nonzero overall exit status. Complete independent languages retain their videos. A language with remaining unfitted groups is not finalized with missing narration. Empty source recognition is ignored only where VAD also found no speech; speech with an unusable transcript fails honestly.

`stage_call()` reserves the worst-case permitted stage charge before starting it. Successful responses settle the estimate; unexpected failures leave the reservation in place. Fit recovery has its own ledger within the whole-run remaining budget. The default full-run ceiling is ₹50 and is configurable. This default does not authorize us to launch a new paid demonstration without the user's approval; tests mock all provider requests.

The `--from-run` path uses a saved source run and validated fit manifest to render offline. It reads no model credentials and makes no model calls. The real demo has been rendered through this path. The fresh full hosted command has not been repeated against the demo, avoiding payment for work we already saved.

`configuration.py` safely reads bounded YAML/JSON mappings. Voice configuration selects documented voices. Glossary hints are sent to Bodhan as a separate labelled context segment; that segment is discarded before synthesis. Preferred terms must appear in the provider result or the unit is flagged; Python never patches target words. Code-mixed mode uses the documented product translation endpoint and rejects truncated output. That optional route has mocked contract tests, not a completed live validation.

**Syntax, briefly:** `argparse` converts command-line flags to typed attributes. A `lambda` is a small function passed to `stage_call()`. `yaml.safe_load()` avoids Python-object construction from configuration. `set(...)` helps reject duplicates. `raise MediaError(code, message)` provides a stable machine-readable failure. An exit code of `0` means outputs completed; nonzero means at least one requested language failed.

**Verification:** a thirty-second synthetic video passes all stages with actual FFmpeg and mocked Bodhan/LLM transports. Another smoke case rejects Bengali credentials while still producing Hindi video/subtitles. Tests block real network access, verify budget reservation, prevent output overwrite and check configuration contracts. The recipe dependency check is clean. The Cookbook's root check currently fails on its unrelated AndroidSpeak example. Docker is unavailable locally, so container setup remains unverified.

## Next quality pass

Review source/translation numerical meaning, technical terminology, naturalness, pause cuts and estimated synchronization. Rebuild affected outputs after approved corrections. Do not confuse physically fitting audio with a fully verified translation. Public demo hosting, a recording, clean-container validation and the final authorized Git/PR/email steps remain submission work.

## Feature 18 — Never insert a source pause inside generated speech

**Files:** `phrase_fit.py`, stricter `place_pcm()` in `audio_output.py`, `fit_recovery.py`, `fit_validation.py`, `fit_qa.py`, `render.py`; `test_phrase_fit.py` and `test_phrase_recovery_integration.py`.

**Problem:** the earlier placement logic tried to preserve a source pause by cutting a generated waveform at a nearby low-energy sample. A quiet consonant or a moment inside a word could pass that check. The user heard “अपनी” split around a pause in Hindi group two. Even a cut between words could incorrectly leave “अपनी” before the pause and “नोटबुक…” after it. Preserving samples and meeting duration limits did not establish a valid linguistic boundary. The earlier description of quiet-point cutting is historical and superseded by this feature.

**Purpose:** remove that fallback entirely. A generated waveform is now indivisible unless we have evidence of independently generated complete phrases. The placement routine may put a whole piece into a later interval or reject a placement, but it never scans waveform energy for an acceptable cut. Failure to fit cannot authorize a cut through speech.

`complete_phrases()` reads Bodhan's existing translation without rewriting it. It partitions only at sentence punctuation or a heading/clause terminator such as a colon, conservatively keeping common abbreviations together. It preserves every character, including whitespace. It does not use a desired character count to decide where a video pause belongs. “अपनी नोटबुक खोलें…” remains one phrase; a single word cannot be detached from it to fill the preceding slot.

`fit_phrases()` synthesizes those exact phrases with Bodhan, using the content cache and a reserved cost allowance. Each atom is sent as a complete request. A preferred character count cannot detach a word from its sentence; text exceeding the actual client input bound must be shortened through the approved English/Bodhan recovery path or rejected. Each complete atom's actual duration is measured. A bounded partition search chooses whole atoms for each allowed source interval, then FFmpeg fits and normalizes each interval independently. Thus normalization/tempo processing cannot invalidate the sample position of a future cut between intervals.

The result records independently processed piece files, their hashes, their exact text and their measured sample boundaries. The final normalized track is an exact concatenation of those pieces. `validate_fit_manifest()` checks the files reconstruct the track and the text reconstructs the translation. It also checks every piece ends at an allowed complete-phrase text boundary, so even independently generated “अपनी” followed by “नोटबुक…” is rejected as a placement arrangement. Only the recorded complete-piece sample boundaries may occur at a playback pause. Old manifests using the former estimated cuts are rejected.

If no complete-phrase partition fits within 1.2×, the existing last-resort English condensation/retranslation path can try a shorter candidate. The same phrase checks apply again. A final failure stays explicit; no arbitrary split is restored to claim success. The renderer uses the validated placement plan directly rather than finding a new cut during rendering.

New phrase audio must be back-transcribed again. It cannot inherit CER from the older waveform just because the written translation stayed unchanged. `fit_qa.py` enforces that distinction. These checks protect against pauses introduced by our code; they do not certify that a provider never mispronounces, omits or unnaturally pauses during its own generation.

**Syntax, briefly:** `@lru_cache(None)` memoizes repeated partition-search states. The search state is the next source slot and the next whole phrase; a transition assigns a consecutive range of complete phrases. A tuple of `(start, end)` indexes phrases, not positions inside a word. Cumulative sample counts form the allowed waveform boundaries. `set` membership checks that a proposed text/sample boundary was explicitly established.

**Tests:** synthetic low-energy audio inside a word must be rejected, regardless of apparent silence. Hindi, Tamil and Bengali punctuation partitions preserve text. The Hindi regression keeps “अपनी नोटबुक…” together. Another test moves “अपनी” to the preceding piece while preserving all characters and confirms validation still rejects it. The production fitter integration test checks this path before English condensation and confirms newly generated speech receives fresh QA. Existing single-slot fitting and language-isolation tests remain relevant.


**November 22 live evidence:** all thirty-six groups pass the strict boundary/sample validator. Nine pause-crossing groups were regenerated; Hindi group two now ends the first piece after “उपयोग किए गए हैं।” and starts the next with the full “अपनी नोटबुक…” instruction. The corrected videos are in `outputs/dubbed-demo-phrase-safe/`, each 329.267 seconds with zero skipped groups. All repaired groups have QA tied to their actual generated waveform hash. There are 448 passing recipe tests. Combined recovery, including the earlier duration work, rejected candidates and content checks, is estimated at ₹5.6174658333 within the original ₹10 cap. Translation/content warnings and estimated sync remain open.


## Feature 19 - Exclude the original mixed soundtrack

**Files:** `render.py`, the final-report field in `pipeline.py`, and the regression in `tests/test_output.py`.

**Problem:** Hindi segment six contained an audible piece of original audio before the generated definition. The renderer had trusted VAD gap labels and copied the source track there. Comparing final PCM with generated-only PCM proved a strong original signal in the gap, even though VAD labelled it non-speech. A speech detector can miss quiet/short words; its output cannot certify a voice-free background.

**Purpose:** prevent the original language from leaking back into any dubbed video. The renderer now allocates a zero-filled full-duration track and writes only validated generated-speech placements into it. It does not call the original-background extraction helper or add the mixed source audio anywhere. Source pauses stay silent. This is a general policy for all videos and languages, not a special mute around the reported timestamp.

The video mux still copies only the original video stream and takes its audio from the newly assembled track. Thus the visual content and duration remain unchanged, while source voice, music and effects are excluded. Restoring music would require a separate verified voice-free background or a separately discussed separation feature; neither is silently assumed.

The run report records `original_audio_mixed: false` and `gap_audio: silence`. The regression fixture intentionally contains audible source sound where its detector claims a pause. It asserts that the final PCM equals the generated-speech-only track and that the protected gap is actually silent. This tests a detector mistake rather than merely trusting a correctly silent fixture.

**Syntax, briefly:** `np.zeros(frames, dtype=np.int16)` creates PCM silence for the exact full-video duration. `track[a:b] = pcm[x:y]` copies only an approved generated-speech interval. The previous addition of an original/background array has been removed. The same narration, translations, timings and phrase boundaries are reused; no provider request is needed to rerender.

**Evidence:** all three corrected videos are in `outputs/dubbed-demo-clean-audio/`; the original-input leakage policy is checked across each full output track and specifically before the Hindi definition. Source music/effects are deliberately absent. Existing content/translation and estimated-sync warnings remain separate.

## Feature 20 — Translation meaning checks and bounded repairs

**Files:** `meaning_checks.py`, `translation_repair.py`, `translation.py`, `pipeline.py`, `fit_recovery.py`; tests in `test_meaning_checks.py`, `test_translation_repair.py` and the pipeline suite.

**Purpose:** preserve the lesson's meaning before paying for speech. Script checks and character similarity cannot establish whether a translated number, instruction or mathematical term is correct. This layer compares Bodhan's translation with the original English through a separate Bodhan back-translation and an English-only Groq review. It checks definitions, numerical relationships, negation, diagram labels, ordinal headings and notebook/write/draw instructions. Neither the back-translator nor the reviewer is treated as ground truth.

`explicit_numbers()` expands contextual English shorthand such as “two forty degrees” to “two hundred and forty degrees”. It requires angle or arithmetic context, so an unrelated appointment at “one twenty” stays unchanged. The original reconciled transcript and its timing references remain intact. Both initial translation and last-resort duration recovery use the normalized English, preventing a shorter rewrite from silently restoring ambiguous numbers.

`before_speech()` runs the meaning gate in the ordinary unattended pipeline. A suspected error permits a bounded English clarification followed by a new Bodhan translation. Groq cannot generate native-language corrections. An unavailable check or a candidate that still changes meaning marks that unit failed, while successful units and languages remain reusable.

`repair_meanings()` applies the same policy to saved fitted outputs. Accepted units keep their exact waveform. Changed text is translated by Bodhan, synthesized as complete phrases, physically fitted and back-transcribed again. Names, labels, numeric phrases and ordinals are guarded before a proposed English edit is accepted; an independent English meaning review also checks instructions and relationships. A failed fit permits bounded English shortening, never a word cut or an arbitrary pause inside generated speech.

**Textbook terms:** the optional glossary supplies reference-checked native spellings to Bodhan as hints; Bodhan still produces the complete final translation. Matching ignores presentation whitespace and punctuation but distinguishes shorter terms contained inside longer ones: Hindi पूरक alone cannot be “found” solely inside संपूरक. Reference provenance is saved alongside the review artifacts. A configured subject glossary is optional, rather than a hardcoded lesson or timestamp rule.

**Verification copies:** back-translation sometimes renames a correctly supplied textbook term. Where the configured native terms are actually present, `verification_terms()` substitutes corresponding opaque concept IDs in copies of the English and translated text used only for checking. This lets the reviewer compare surrounding numbers, definitions and instructions without the back-translator mistranslating an already checked term name. The actual translation, subtitles and speech input are unchanged. Presence and ordering are supporting evidence, not proof that all grammar or term usage is correct.

**Budget and recovery:** reserve an upper-bound allowance before a paid call, settle known usage afterwards and preserve uncertain charges conservatively. A cache refusal before transport releases only the new reservation; it never erases an earlier uncertain request. Successes are reused by content and settings. An unusable saved request is retained rather than silently resent; the bounded repair may instead propose a clearer English input. Every resumed pass writes a new manifest revision and separate waveform-verification folder, preserving previous evidence and avoiding stale QA on changed audio. The cumulative user-approved cap applies across revisions.

**Syntax, briefly:** a dictionary comprehension creates a glossary for the current target language. `dataclasses.replace()` derives bounded request settings without mutating shared configuration. `Decimal` avoids floating-point rounding in cost limits. `try`/`except MediaError` separates a recoverable unit failure from a whole-run crash. `deepcopy()` keeps the original fitted manifest immutable; accepted replacements are written into a new result. Hashes and request journals tie checks to the exact text and audio they assessed.

**Tests and limits:** regression cases cover contextual numbers, digits in back-translations, unchanged-English definitions, substring-confusable glossary names, invented/missing review identities, independent language failures, verification-copy isolation and no-call reservation accounting. These checks reduce specific failures; they do not certify native pronunciation, naturalness or exact synchronization. See the latest quality manifest for accepted and unresolved repairs rather than treating the existence of a rendered video as a quality pass.

## Feature 21 — Explicit quality-target evidence and persistent stage logs

**Files:** `quality_targets.py`, `pipeline.py`, `test_quality_targets.py`, additional assertions in `test_pipeline.py`.

**Purpose:** make every assignment quality target visible without turning missing evidence into a pass. The earlier runtime boolean timed the current invocation even for saved-audio rendering. That could make a quick offline render look like the required full hosted benchmark. The pipeline now records its execution scope and attaches a seven-target summary to `report.json`. Saved-audio rendering, an incomplete run and a non-reference workload leave the hosted benchmark unverified. A measured slow complete reference run is a miss rather than an unknown.

The summary reads measured speed, validated piece limits, source-group coverage, per-group recognition CER, video-duration error and track loudness. Out-of-range measurements are misses; absent data is unverified/incomplete. Reporting all CER values satisfies that reporting requirement without asserting the audio is perfect. Estimated source bounds never establish the separate ±300 ms acoustic-sync target.

The stage-saving function also appends structured status and elapsed-time records to top-level `events.jsonl`, while keeping structured stdout and the existing per-segment render/provider journals. This establishes a persistent per-run stage trail. Older saved outputs are not given invented historical stage events.

**Syntax, briefly:** `type(value) in {int, float}` plus `math.isfinite()` rejects booleans, missing values and nonfinite measurements. Dictionary comprehensions organize measurements by language. `None` serializes as JSON null for an unverified legacy runtime flag. Opening a file with mode `a` appends one JSON object per line without erasing previous stage events.

**Tests:** a fast saved-audio render cannot pass hosted runtime or source sync; a slow complete hosted reference run is a measured miss; incomplete runs cannot establish that benchmark. Missing languages/CER and out-of-range loudness do not pass. The actual-media 30-second smoke checks saved stage events and treats its short workload as unverified for the five-minute runtime target.

## Feature 22 — Verified isolated Docker setup

**Files:** existing `Dockerfile` and `.dockerignore`, `docs/docker_validation.md`; one corrected preparation-only description in `__main__.py`.

**Purpose:** ensure evaluators can install the recipe independently of this Mac's virtual environment and local FFmpeg. The image starts with Python 3.12, installs system FFmpeg/FFprobe, selects CPU PyTorch on Linux, installs the pinned requirements, then installs the application as a wheel. All 463 tests pass inside the Linux ARM64 image. The required CLI and an actual demo-file preparation also run there. This is container setup evidence; the hosted full-run benchmark remains separate.

`.dockerignore` excludes private environment files, host dependency caches, generated output/media and Git metadata from the build context. The image's source-copy instructions copy application source rather than those private artifacts. Runtime checks also confirm no API credentials were supplied, no private `.env` or host venv was copied, and the application imports from its installed package. Bundled VAD loads offline.

**Syntax, briefly:** `FROM` chooses the base runtime; `RUN` executes installation steps while building; `COPY` adds the declared package inputs; `ENTRYPOINT` makes arguments after the image name become arguments to `python -m bodhan_dub`. Docker `--network none` prevents validation from making external calls. A `:ro` bind mount exposes input media/tests read-only; a separate writable output mount receives artifacts. `--entrypoint python` switches the executable for container-only diagnostics/tests. The normal hosted command needs its private environment file and network access, neither of which was supplied during validation.

The prep-only report previously said translation and subtitles were unimplemented, an obsolete description after the full pipeline was added. It now says these operations are not performed *in that mode*. No narration or translation changed. The final image was rebuilt and tested after that wording correction.

**Limits:** validated Linux ARM64 on Docker Desktop, not Linux AMD64 or a new Git clone of the final submitted commit. No image was published, no API credit was spent, and no hosted performance target was inferred from offline container tests.

## Feature 23 — Recover the failures exposed by the integrated live run

**Files:** `source_recovery.py`, `pipeline.py`, `translation_repair.py`, `meaning_checks.py`, `reconciliation.py`, `grouping.py`, CLI in `__main__.py`, plus corresponding source/translation/reviewer and full-command tests.

**Purpose:** the first live invocation exposed gaps that individual clients and the original mocked smoke did not cover. A successful HTTP response contained unusable source recognition. The main command also disabled the existing boundary-corroboration adapter, and partial translated documents could reach speech generation before their missing units were resolved. English reasoning models could consume their output budget without producing the expected plan; a valid JSON array of review checks could also crash object-only handling. These are integration defects, not evidence that the earlier tests established every provider behavior.

`recover_source()` validates source text before reconciliation. At most three unusable source windows can receive a recovery pass. Each is split into two shorter, overlapping WAV requests using the original PCM. The source samples and timing envelope are retained, the original provider response stays in its journal, and the returned words still come entirely from Bodhan. A failed shorter result stops this bounded recovery; no LLM invents missing speech. Recognition context slicing is distinct from the prohibited splitting of generated speech inside words.

`boundary_recovery()` wraps the existing Bodhan boundary adapter in the same spending ledger. The main correction call now permits at most two corroboration calls for unresolved overlap conflicts, including protected terms. Only source-backed overlap choices are accepted. If source windows remain blocked, the command stops before translation and synthesis instead of paying to generate knowingly incomplete narration. The deterministic fallback also validates overlap deletions jointly, preventing adjacent greedy deletions from removing each other's supporting words.

`recover_translation_units()` permits one retry of a known partial translated document. The translation client's successful unit files remain cached, so the provider receives only pending IDs. Previously accepted target text is unchanged. A language still incomplete after native translation/meaning checks is excluded from speech jobs, while complete independent languages can continue. Original parse errors remain recorded for audit.

The English review parser accepts either its expected object or a bare list of well-shaped check records, which it wraps without changing their content. The judge still validates every expected ID and decision. Unrelated JSON is rejected. Malformed/incomplete English responses have a bounded retry; safe error metadata is saved without provider bodies, keys or authorization headers. Known reasoning-model names use low reasoning effort so a compact plan can fit the output allowance; an endpoint rejecting that optional parameter gets a standard chat-request fallback. Other model names receive the ordinary portable arguments. English planning/review calls share request pacing and bounded 429 cooldown handling; an exhausted retry is not multiplied by nested reviewer retries.

The optional `--cache-dir` selects a persistent provider-result cache across new output folders. It does not authorize overwriting reports or silently repeating unknown billed outcomes. Every cache is still validated by its existing content/model/settings identities and file locks. This is result reuse, not a claim that every local stage resumes from a checkpoint. Runs explicitly using that shared cache are labeled separately, so they cannot establish a fresh hosted runtime benchmark.

**Syntax, briefly:** WAV offsets are integer sample positions; dividing by 16000 yields source seconds. A closure wraps the adapter while retaining its client and budget. `Decimal` reservations precede paid recovery. `try`/`except` distinguishes unusable model data from accepted source text. Copying the list of decisions lets the overlap validator test one candidate against all prior accepted decisions before committing it. JSON wrapper normalization changes schema only, never native wording. Cached failures remain auditable when a changed, versioned planning policy is deliberately tried.

**Regression coverage:** the full command receives a garbled initial ASR result, makes only two shorter recovery requests and completes mocked media output. Another full-command case proves unresolved source conflicts cause no translation or speech calls. Unit tests check PCM/context preservation, costs, recovery exhaustion, boundary budget handling, partial native ID recovery with successful-cache reuse, malformed English JSON retries, check-array validation and portable reasoning-parameter fallback. See the latest test log for counts. Model errors can still exhaust a bounded recovery; that remains an explicit failure rather than a promise of perfect output.

## Feature 24 — Keep source thoughts intact across recognition windows

**Files:** `source_punctuation.py`, the fallback path in `pipeline.py`, `test_source_punctuation.py` and the main mocked-media integration tests.

**Problem:** retaining every recognized word does not establish sentence boundaries. The earlier fallback inserted a period at each audio-window end. In the actual lesson this left one unit ending “a right” and the next beginning “angle is exactly ninety degrees”. Translating those fragments independently can move a definition across IDs or lose meaning. The successful earlier manually reviewed render did not establish that the generic fallback would produce the same grouping.

**Purpose:** separate overlap selection from punctuation. First retain the source-backed, jointly validated word sequence. Then let the English LLM propose punctuation on that fixed sequence. Audio-window endings have no special grammatical status. The grouping stage can now combine complete source sentences into thoughts instead of being handed artificial window fragments.

The LLM's text is never accepted as a rewritten transcript. Python aligns its lexical anchors against the selected source words with `difflib.SequenceMatcher`, uses only supported punctuation-position hints and reconstructs every output word from the original source references. A proposed spelling correction, insertion or deletion is ignored as content. A high anchor-coverage requirement rejects an unrelated or heavily rewritten proposal; it is not permission to edit any percentage of source words. The report records ignored proposal edits and confirms full source-word preservation. If both bounded proposals are unusable, planning fails explicitly before translation.

The live lesson check retains all 550 selected source words and produces 47 sentence records. Its complete angle-definition sentence crosses recognition windows. Some model-proposed wording changes were deliberately not applied. This preserves ASR imperfections too: punctuation is not an invented correction or proof that ASR captured the original audio perfectly.

Harmless compound hyphenation such as “non-common” is treated as presentation when mapping hints. The source's words still remain as recognized, and a final question mark is preserved. Only allowed sentence punctuation is emitted. Every resulting span is checked again by the existing source-plan validator, including overlap exclusion support and ordering.

**Syntax, briefly:** `SequenceMatcher(..., autojunk=False)` compares token sequences, not audio or timestamps; it is not an external alignment model. An equal block maps a punctuation position back to its exact original token. Replaced/deleted/inserted proposal spans are never used as word content. A dictionary stores the permitted punctuation after selected source offsets. Consecutive references in the same recognition window become one span; a grammatical sentence may contain spans from more than one window.

**Tests:** a right-angle definition remains one sentence across two windows. An intentionally omitted model word is restored from the original source, with all sixty test words preserved. Unrelated text, unsupported formatting and invalid schemas are rejected. Bounded correction and compound hyphen/final-question behavior are checked. The full thirty-second mocked pipeline executes the actual punctuation helper with only the model response mocked.


## Feature 25 — Reuse semantic checks and bound contextual translation repairs

**Files:** `translation_repair.py`, `meaning_checks.py`, the main pipeline and their regression tests.

**Purpose:** continuing a run should not repeat a successful native back-translation or English review just because its output directory changed. The optional shared cache now contains the native back-translation and contextual repair caches plus content-keyed English review proofs. Each new run still writes its own translation and meaning reports. Provider URL/model and complete input identities distinguish cached decisions, so changing text or settings cannot silently reuse an unrelated judgment.

An unchanged English clarification is no longer rejected before calling Bodhan. Clear source English can still benefit from a distinct contextual translation request containing the complete lesson and configured terminology. This does not authorize the English LLM to write native text: Bodhan produces every target candidate, and each candidate must pass the same back-translation and meaning checks. There are at most two contextual repair candidates after the initial translation. An unresolved candidate leaves its language incomplete and prevents that language from receiving knowingly incomplete speech.

**Syntax, briefly:** `Path` joins build cache locations; SHA-256 of sorted JSON makes a stable identity from content and model configuration. `cache_root=None` retains the original per-run behavior. A two-iteration `for` loop bounds repair attempts; the second proposal receives the preceding failed check as feedback. `setdefault` retains a specific failure reason rather than replacing it with a generic one. Cached English proposals still pass the deterministic number/label guards and English factual review before use.

**Verification:** unchanged-English contextual retry, a second candidate after a failed first candidate, and shared review reuse across separate run reports have regression coverage. All **488 tests pass locally and in the Linux ARM64 container**, with networking disabled for the container suite. Live validation is still running; the test count does not certify native naturalness or final acoustic synchronization.


A completed provider response that failed native validation is distinct from an in-flight or unknown request. The translation client now attaches the saved journal state to its no-replay error. The repair loop may use its remaining candidate for a changed English clarification after a *completed-invalid* result; it never replays that identical request. Unknown/in-flight results still stop immediately. The second clarification receives explicit guidance about complete clauses, literal protected quantities and independently stated list items. Native wording and the acceptance checks remain unchanged. Regression cases cover both journal states, and all 488 tests pass locally and offline in Docker.


## Feature 26 — Use bounded sentence groups when the English planner falls back

**Files:** `grouping.py` and `test_grouping.py`.

**Purpose:** the old fallback ended most groups after a nontrivial sentence, leaving many small timing slots even when adjacent complete sentences belonged to connected narration. More slots increase local duration pressure and can leave a short question or definition without useful context. The fallback now accumulates adjacent complete source sentences within the same existing bounds: sixty words, five hundred characters and a forty-five-second source-window envelope. It still separates skipped source records and definite long gaps. It never cuts a source sentence merely to meet a limit; an oversized existing sentence remains intact and explicitly flagged.

The existing connected-group pass can retain a grammatical continuation across a planned group boundary, subject to its own timing bound. On the saved lesson source, the bounded fallback produces thirteen groups and that pass joins one continuation, yielding twelve. Every selected source word remains in order. Those counts are an observation for this input, not a hardcoded target or hidden-video rule. Punctuation and timing remain estimated; merging narration does not create acoustic word timestamps.

**Syntax, briefly:** the loop tracks the beginning of the current group and asks whether adding the next whole sentence exceeds a limit. It emits an end index only at a boundary forced by those limits, a source-status change or a definite observed gap. The versioned planning identity distinguishes this fallback policy from the earlier one; old failed planner attempts remain saved rather than being overwritten.

**Verification:** tests cover adjacent substantial sentences, a forced size boundary, a long source gap and an intervening failed source record. All **490 tests pass locally and in Docker**, with container networking disabled. The new live validation remains separate evidence; this change alone does not certify all translations or physical fits.


## Feature 27 — Recover a completed-invalid translation document in smaller batches

**Files:** `translation.py`, `translation_repair.py`, `meaning_checks.py` and their tests.

**Purpose:** a provider may return HTTP 200 with unusable labels for the whole translated document. That differs from a valid document with one missing segment. The original partial-ID recovery did not handle an empty language result after total parsing failure. It now recognizes known completed-invalid responses and gives that language one recovery pass using two smaller source documents. Each contains complete source units; IDs and target-script/digit/glossary checks stay in force. Successful halves are combined deterministically, with no target rewriting. An unknown or in-flight journal is excluded from this recovery.

The original failed document and its cost remain in the run history. Each smaller request has its own input-derived cache identity and budget reservation. A continued run may reuse those successful halves even though the original larger request is still marked invalid. It never resets that original journal or blindly resends it. Failure of a half remains explicit and does not stop other independent languages.

The English reviewer now also receives the dictionary mapping verified terminology placeholders to their full English source phrases. A placeholder may represent a compound term that already denotes a pair or group. Giving the reviewer that identity avoids making it reason about an opaque marker as if it were a new atomic object. This is English metadata from the configured glossary, not native target text. Number, relation, negation and coverage checks remain active; the mapping is not an unconditional acceptance override.

**Syntax, briefly:** `(len(active)+1)//2` chooses a balanced split without dividing any sentence. A dictionary lookup joins accepted targets back to their original IDs. The client exposes a journal state as safe error metadata. `previous_request_state` distinguishes a known invalid response from uncertainty. The review payload's English alias mapping changes the content hash, preventing reuse of a judgment made without that context.

**Verification:** an actual TranslationClient with mocked HTTP receives invalid XML for four units, recovers using two smaller documents and reuses both halves on a second invocation with no further transport calls. Separate cases prove unknown requests do not trigger this fallback. A review test checks that full English term identities are supplied while native text stays out of LLM messages. All **494 tests pass locally and offline inside Docker**. Live validation is separate and remains in progress.


## Feature 28 — Verify disputed translations and retry complete statements

A translation review can disagree because the back-translator misread a glossary placeholder. If a masked review flags a problem, Bodhan can back-translate the intact target for a second English check. The same meaning criteria apply and the report records which review passed.

If a known failed translation still needs repair, the command can request short, complete statements separately and keep their order. Each statement receives glossary hints on the first occurrence of a term. The assembled passage must contain every required glossary term. The English label guard normalizes typographic spacing before checking that diagram labels were preserved. English rewrites still pass number, label and factual checks before being sent. The assembled target must pass meaning review before speech generation. Cached written-out numbers follow the same policy as new translations. Uncertain requests retain their billing reservations and stop recovery.

The isolated lesson checks accepted a revised Hindi diagram passage, the original Tamil reflex-angle wording after an unmasked review, and revised Tamil and Bengali supplementary-angle passages. These checks used the existing source and provider responses. General behavior is tested separately through the command.

The two targeted regressions verify glossary scoping and source order in independent requests, and acceptance only after the intact target passes review. The full offline container suite passed 501 tests.


## Feature 29 — Reserve synthesis costs only for missing audio

The full command used to reserve three attempts for every speech job before checking the cache. That could stop a continuation even when much of its audio was already available. SpeechClient now checks the journal and the waveform hash first. A verified cache hit returns with zero new cost.

For a cache miss, the client reserves the maximum cost of its configured attempts immediately before contacting Bodhan. The per-clip cache lock stays held throughout this decision. A separate lock serializes changes to the shared spending ledger when several worker threads are active. Completions and known failure attempts settle at their estimated charge. A request with an ambiguous outcome remains journaled and is not replayed automatically.

Regression tests cover a mixed cached and new batch under a tight cap, rejection before transport when the cap would be exceeded, and an ambiguous request that must be charged conservatively without being replayed. The ordinary full command uses this same client path.


## Feature 30 — Recover remaining fits automatically

English word counts guide shortening. The final acceptance check measures the generated audio against the actual time slots. The fitter still requires shorter English and a separate judgment that facts are preserved. Each number at the ends of a range is protected independently, Unicode label spacing is normalized during label checks, and another candidate can be tried after a failed synthesis without replaying an uncertain request.

The full command shares its speech cache with the fitter, so a complete phrase synthesized earlier can be placed again without requiring another generation. An optional fitting-stage continuation verifies that the previous manifest belongs to the exact same source artifacts, preserves successful placements and limits paid work to unresolved passages. The final renderer still verifies every placement and waveform.

Regression coverage includes number-range preservation and a continuation that reuses an accepted placement without any hosted request. Direct learner instructions have an explicit guard after a draft dropped a writing instruction. If a returned translation omits a required glossary term, the fitter retries the shortened passage as independently translated complete statements and validates combined terminology before synthesis.


A last-stage fallback may extend an earlier slot into the start of a long internal pause, after ordinary complete-phrase partitions have failed. It takes at most 2.25 seconds and leaves at least half the protected gap and at least 2.5 seconds silent. Later slot start times and the 1.2× speed limit stay fixed. On the demo, the final Tamil passage retained about 2.69 seconds of protected silence. The continuation reused its prior fitted candidate only after matching the accepted English plan, recorded synthesis hash and measured duration.

The verified result contains 36 fitted groups, zero skips, three fully decodable MP4s and three validated SRT files. All 507 tests passed locally and in Docker. The result used hosted stages and automatic continuations, so a fresh hosted runtime and exact acoustic sync remain unverified.

### Estimated boundaries between overlapping transcription windows

The timeline estimates word positions independently within each transcription
window. Near a shared boundary, the next unit can appear to start before the
previous unit even when the source windows are in chronological order. The
timeline now places the transition inside the intersection of the two estimated
ranges. Both units keep positive duration, and protected pauses are still
excluded from their usable slots. Reversed window order and invalid references
still fail validation.

In `timing.py`, `max()` selects the later of the two estimated starts. Averaging
that value with the earlier end gives a shared boundary that closes one unit
and opens the next. These positions remain estimates.

When a completed back-translation batch has malformed output, the meaning
check keeps the returned units and requests each missing unit individually.
Each request goes through the existing budget check. An unknown batch outcome
still blocks recovery. During fitting, the next English shortening attempt
receives the actual validation message, including the missing instruction.

The command records language failures as soon as translation verification ends.
Only languages with complete verified text enter speech generation. Speech
generation and verification each use two workers with the existing request
pacing. The budget lock makes each reservation and journal update atomic
across speech workers.
