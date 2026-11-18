# syntax=docker/dockerfile:1
FROM python:3.11-slim

LABEL maintainer="Bodhan AI Integration Team"
LABEL description="Bodhan MCP Tool Server and Autonomous Task Agent"

# Avoid interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install system dependencies: ffmpeg for audio windowing/conversion and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root application user
RUN useradd -m -u 1000 -s /bin/bash bodhan
WORKDIR /app

# Install pinned Python dependencies
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock

# Copy application source code
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY README.md ./

# Install application package in editable mode
RUN pip install --no-cache-dir -e .

# Create cache and output directories with correct permissions
RUN mkdir -p .cache out samples && chown -R bodhan:bodhan /app

# Switch to non-root user
USER bodhan

# Default environment: live calls require explicit opt-in at runtime
ENV BODHAN_ALLOW_LIVE=0

# Default entrypoint runs the MCP server over stdio
ENTRYPOINT ["python", "-m", "bodhan_mcp"]
