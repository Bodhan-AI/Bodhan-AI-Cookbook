# Self-contained container for Bodhan MCP Tool Server and Autonomous Task Agent
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System dependencies: ffmpeg enables automatic conversion of .ogg/.m4a/.mp3/.webm audio
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pinned Python dependencies first for layer caching
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source, tests, and recipe assets
COPY src/ ./src/
COPY tests/ ./tests/
COPY run_demo_tasks.py README.md .env.example ./

# Install package so `python -m bodhan_mcp` and `python -m bodhan_agent` work from any directory
RUN pip install --no-cache-dir --no-deps -e .

# Default entrypoint: standalone MCP server over stdio
# Override to run the task agent, e.g.:
#   docker run --rm --env-file .env -v "$PWD/inputs:/data" -v "$PWD/out:/out" \
#     bodhan-tool-agent python -m bodhan_agent --task "..." --files /data --out /out
CMD ["python", "-m", "bodhan_mcp"]
