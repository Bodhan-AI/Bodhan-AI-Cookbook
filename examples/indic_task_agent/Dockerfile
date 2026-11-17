# Reproducible runtime: Python 3.12 + ffmpeg (audio/video) + Noto fonts (only for building demo inputs).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-lock.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements-lock.txt && pip install --no-cache-dir --no-deps -e .
COPY tests ./tests
COPY scripts ./scripts
COPY tasks ./tasks

ENV PYTHONUNBUFFERED=1 BODHAN_CACHE_DIR=/cache BODHAN_MCP_OUTPUT_DIR=/tmp/bodhan_mcp
VOLUME ["/cache"]

# Default: the MCP server on stdio (docker run -i ...). For the agent, override the command:
#   docker run --rm --env-file .env -v "$PWD/inputs:/inputs" -v "$PWD/out:/out" indic-task-agent \
#     python -m bodhan_agent --task "..." --files /inputs --out /out
CMD ["python", "-m", "bodhan_mcp"]
