# Docker validation

Initial setup verified November 22; latest regression verified November 23 on Docker Desktop 4.99.0 / Docker Engine 29.9.3, with a Linux ARM64 container on this Apple Silicon Mac. The Dockerfile built a new environment without using the host virtual environment. The final locally tagged image is `bodhan-dub:latest` (also `bodhan-dub:validation`). No registry image was published.

## Verified checks

- Python 3.12.16, CPU PyTorch 2.8.0+cpu, Silero VAD 6.2.3, NumPy 2.2.6, OpenAI client 2.8.1 and PyYAML 6.0.2.
- FFmpeg and FFprobe 7.1.5 are installed; dependency consistency passes.
- The application imports from the installed wheel in `site-packages`, rather than relying on the host checkout or an editable host install.
- Required CLI flags are available. Bundled Silero VAD loads with networking disabled; no GPU or model download is required at runtime.
- All 507 recipe tests pass inside the container, including positive VAD coverage, actual FFmpeg operations, the thirty-second mocked end-to-end run and partial-language failure case.
- The default Docker entrypoint inspected the actual supplied demo and extracted aligned 16 kHz mono PCM. Video duration: 329.266667 seconds; PCM duration: 329.2666875 seconds. Preparation is explicitly marked incomplete dubbing and made zero model calls at zero API cost.
- Runtime validation used `--network none` and no API credentials. `.env`, the host venv, generated outputs and Git metadata are absent from the image.

Build-time networking downloaded the base image, system packages and pinned Python dependencies. Runtime validation networking was disabled. A hosted run requires networking plus privately provided credentials and a separately approved API budget; it was not performed by these checks.

## Reproduce

From `examples/video_dubbing`:

```sh
docker build -t bodhan-dub .
docker run --rm --network none bodhan-dub --help
```

Run the full pipeline after supplying a private environment file and choosing an appropriate paid-run ceiling:

```sh
docker run --rm \
  --mount type=bind,src=/private/path/.env,dst=/run/secrets/bodhan.env,readonly \
  -v /path/to/media:/media:ro \
  -v /path/to/results:/results \
  bodhan-dub --input /media/English.mp4 --langs hi,ta,bn --out /results/run \
  --env-file /run/secrets/bodhan.env
```

The output directory `run` must be new or empty. The media mount is read-only. Never copy the private `.env` into the image or repository. The default run tracks costs. Supply `--max-cost-inr` only if you want an explicit spending limit.

To rerun container tests without hosted access, mount only the tests and an authorized speech fixture:

```sh
docker run --rm --network none \
  -v "$PWD/tests:/recipe/tests:ro" \
  -v /path/to/authorized/speech.wav:/fixtures/speech.wav:ro \
  -e BODHAN_TEST_SPEECH_WAV=/fixtures/speech.wav \
  -e OMP_NUM_THREADS=1 \
  --entrypoint python bodhan-dub \
  -m pytest /recipe/tests -q -p no:cacheprovider
```

Local ignored evidence is in `outputs/docker-validation/`: `build.log`, `tests.log`, `probe.log`, `image.json` and `prepared-final/report.json`. The build log records the final cached rebuild after correcting an outdated preparation-only message; the initial build installed dependencies from scratch. No new Dockerfile dependency changes were necessary.

## Limits of this validation

Linux AMD64 was not tested. A literal new Git clone of the final submitted commit has not been performed; this test used the recipe's Docker build context with private/generated files excluded. This establishes isolated container setup and offline/mocked execution, not fresh hosted runtime, perfect translation or ±300 ms acoustic sync.

The latest fitting recovery image passed all 507 tests with networking disabled. Its build and test evidence is in `pause-fit-build.log` and `pause-fit-tests.log`.
