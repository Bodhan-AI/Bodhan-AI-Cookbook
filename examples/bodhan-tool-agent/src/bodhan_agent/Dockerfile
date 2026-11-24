FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md Makefile .env.example ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install -e ".[dev]"

# Default to standalone MCP stdio server; override command to run bodhan_agent.
CMD ["python", "-m", "bodhan_mcp"]