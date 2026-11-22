# Reproducible runtime for the dubbing pipeline.
#   docker build -t bodhan-dub .
#   docker run --rm --env-file .env -v "$PWD/data:/data" bodhan-dub \
#     --input /data/video.mp4 --langs hi,ta,bn --out /data/out
#
# amd64 on purpose: silero-vad-lite ships no Linux ARM wheel (on ARM the pipeline would fall
# back to the energy VAD). Docker Desktop on Apple silicon runs this image under emulation.
FROM --platform=linux/amd64 python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "bodhan_dub"]
