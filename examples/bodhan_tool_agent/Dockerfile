# Reproducible runtime for the agent and the MCP server.
#   docker build -t bodhan-tool-agent .
#   docker run --rm --env-file .env -v "$PWD/inputs:/data/inputs" -v "$PWD/out:/data/out" bodhan-tool-agent \
#       python -m bodhan_agent --task "..." --files /data/inputs --out /data/out
#   docker run --rm -i --env-file .env bodhan-tool-agent python -m bodhan_mcp      # MCP over stdio
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt

COPY tests ./tests
COPY evals ./evals
COPY scripts ./scripts

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    BODHAN_CACHE_DIR=/data/cache
RUN useradd --create-home agent && mkdir -p /data && chown agent /data
USER agent
CMD ["python", "-m", "bodhan_mcp"]
