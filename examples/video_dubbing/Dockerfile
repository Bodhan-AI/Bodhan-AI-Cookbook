# Reproducible container for the dubbing pipeline (CPU only; every ASR/translation/TTS call goes to Bodhan's
# hosted API, Demucs separation runs locally).
#
#   docker build -t bodhan-dub .
#   docker run --rm --env-file .env -v "$PWD/media:/data" bodhan-dub \
#       --input /data/video.mp4 --langs hi,ta,bn --out /data/out
#   docker run --rm --entrypoint python bodhan-dub -m pytest -q          # unit tests inside the image
#
# Add --user "$(id -u):$(id -g)" on Linux so the outputs belong to you instead of root.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TORCH_HOME=/opt/torch

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pinned dependencies first (cached layer; torch/torchaudio come from the CPU wheel index named in the file),
# then the recipe itself, so a code change does not reinstall torch.
COPY requirements.txt pyproject.toml ./
RUN grep -v '^-e ' requirements.txt > /tmp/requirements-deps.txt \
    && pip install -r /tmp/requirements-deps.txt
COPY src ./src
COPY tests ./tests
RUN pip install --no-deps -e .

# Demucs htdemucs weights (~80 MB) are baked in, so a run never waits for the download.
RUN python -c "from bodhan_dub.separate import load_model; load_model()" \
    && chmod -R a+rX /opt/torch

WORKDIR /data
ENTRYPOINT ["python", "-m", "bodhan_dub"]
CMD ["--help"]
