# Dockerfile — Bodhan Tool Server & Task Agent

FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Download NLTK data
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('punkt_tab')"

# Copy source
COPY src/ src/
COPY tests/ tests/

# Default command: MCP server on stdio
CMD ["python", "-m", "bodhan_mcp"]
