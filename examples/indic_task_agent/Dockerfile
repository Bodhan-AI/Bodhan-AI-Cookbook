# Reproducible environment for the Bodhan MCP server and task agent.
#   docker build -t indic-task-agent .
#   docker run --rm -i --env-file .env indic-task-agent                      # MCP server on stdio
#   docker run --rm --env-file .env -v "$PWD/sample_data:/data" -v "$PWD/out:/out" indic-task-agent \
#       python -m bodhan_agent --task "Read this paragraph aloud in Bengali" --files /data/paragraph --out /out/run1
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home agent && mkdir -p /out /work && chown agent /out /work
USER agent
# relative paths such as the brief's `--out ./out` land in /work, which the non-root user owns
WORKDIR /work
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8

CMD ["python", "-m", "bodhan_mcp", "--artifacts-dir", "/out/mcp_artifacts"]
