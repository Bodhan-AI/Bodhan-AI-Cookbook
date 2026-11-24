# Task agent and MCP server in one image.
#
#   docker build -t bodhan-tool-agent .
#   docker run --rm --env-file .env -v "$PWD/inputs:/data" -v "$PWD/out:/out" \
#       bodhan-tool-agent --task "Transcribe this Hindi voice note" --files /data --out /out
#
# MCP server over stdio (for an MCP client that launches docker):
#   docker run --rm -i --env-file .env -v "$PWD/inputs:/data" --entrypoint python bodhan-tool-agent -m bodhan_mcp
# MCP server over streamable HTTP with a bearer token (BODHAN_MCP_TOKEN in .env):
#   docker run --rm -p 8765:8765 --env-file .env --entrypoint python bodhan-tool-agent \
#       -m bodhan_mcp --http --host 0.0.0.0 --port 8765
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BODHAN_CACHE_DIR=/cache

WORKDIR /app

# Dependencies first so code edits do not invalidate the install layer.
COPY pyproject.toml README.md ./
COPY src ./src
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY tasks ./tasks
COPY scripts ./scripts

VOLUME ["/cache"]
ENTRYPOINT ["python", "-m", "bodhan_agent"]
CMD ["--help"]
