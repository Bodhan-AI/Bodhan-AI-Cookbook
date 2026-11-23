# Bodhan tool server + task agent. Build from this folder:
#   docker build -t bodhan-task-agent .
# Agent:   docker run --rm --env-file .env -v "$PWD/inputs:/data:ro" -v "$PWD/out:/out" bodhan-task-agent \
#            python -m bodhan_agent --task "..." --files /data --out /out
# Server:  docker run --rm -i --env-file .env bodhan-task-agent python -m bodhan_mcp
FROM python:3.12-slim

# ffmpeg: audio/video decoding, loudness normalisation and MP3 encoding. Nothing else is needed:
# PDFs are rasterised by pypdfium2 (a wheel), so there is no poppler dependency.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    BODHAN_CACHE_DIR=/cache
WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./
COPY src ./src
RUN pip install -r requirements.txt \
 && useradd --create-home --uid 1000 agent \
 && mkdir -p /cache /out && chown agent:agent /cache /out
USER agent

# Default: the MCP server on stdio, so any MCP client can launch the container directly.
CMD ["python", "-m", "bodhan_mcp"]
