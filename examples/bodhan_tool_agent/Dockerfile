FROM python:3.11-slim

# Install system dependencies: ffmpeg for audio chunking/merging
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1
ENV BODHAN_BASE_URL=https://api.bodhan.ai

EXPOSE 8000

# Default to STDIO MCP server, override with CLI or HTTP server as desired
CMD ["python", "-m", "bodhan_mcp"]
