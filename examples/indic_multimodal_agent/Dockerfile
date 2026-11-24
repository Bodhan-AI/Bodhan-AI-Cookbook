# Dockerfile for Bodhan MCP Server and Task Agent
FROM python:3.11-slim

# Install system dependencies: ffmpeg for audio processing, fonts for layout rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install dependencies and package
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Create directory for outputs
RUN mkdir -p /app/out /app/samples

ENV PYTHONUNBUFFERED=1

# Default entrypoint: MCP server over stdio
CMD ["python", "-m", "bodhan_mcp"]

