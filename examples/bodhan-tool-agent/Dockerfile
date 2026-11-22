# Dockerfile for Bodhan Tool Server and Task Agent
FROM python:3.11-slim

# Install system dependencies (ffmpeg is required for audio conversions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications and install
COPY requirements.txt pyproject.toml README.md ./
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .

# Copy source code and scripts
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY .env.example ./

# Environment defaults
ENV PYTHONUNBUFFERED=1

# Default command shows agent help
CMD ["python", "-m", "bodhan_agent", "--help"]
