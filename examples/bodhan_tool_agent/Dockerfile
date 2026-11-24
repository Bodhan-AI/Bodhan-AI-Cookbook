# Container environment for Bodhan MCP Server & Task Agent evaluation
FROM python:3.11-slim

# Install system dependencies (ffmpeg and libsndfile for robust audio handling)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    git \
    make \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project definition and source
COPY pyproject.toml .env.example README.md ./
COPY src/ src/
COPY tests/ tests/
COPY scripts/ scripts/

# Install dependencies in editable mode
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -e . && \
    pip install --no-cache-dir pytest pytest-asyncio

# Default entry point
CMD ["python", "-m", "bodhan_agent.eval"]
