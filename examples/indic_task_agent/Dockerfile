# Clean-room image for the agent and the MCP server.
#   docker build -t indic-task-agent .
#   docker run --rm --env-file .env -v "$PWD/sample_data:/data" -v "$PWD/out:/out" indic-task-agent \
#       --task "Read this paragraph aloud in Bengali with a female voice" --files /data/paragraph.txt --out /out
#   docker run --rm -i --env-file .env --entrypoint python indic-task-agent -m bodhan_mcp   # MCP server on stdio
FROM python:3.12-slim

# ffmpeg/ffprobe decode, cut, join and encode audio (Debian's build has no speech-recognition filter).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt
COPY tests ./tests
COPY evals ./evals

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 BODHAN_CACHE_DIR=/app/.cache/bodhan_mcp
ENTRYPOINT ["python", "-m", "bodhan_agent"]
