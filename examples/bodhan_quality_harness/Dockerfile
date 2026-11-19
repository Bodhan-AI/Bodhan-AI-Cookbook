# Reproducible image for the harness. No system dependencies are needed: audio
# checks read PCM WAV directly, so there is no ffmpeg to install.
#
#   docker build -t bodhan-eval .
#   docker run --rm -v "$PWD:/data" -v "$PWD/.env:/app/.env:ro" bodhan-eval \
#     --dataset /data/data/demo.jsonl --langs hi,ta,bn --out /data/report

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first so the layer is cached across code changes.
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY . .

# Never bake secrets in: .env is mounted at run time and is git-ignored.
ENTRYPOINT ["python", "-m", "bodhan_eval"]
CMD ["--help"]
