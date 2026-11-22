FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY requirements.txt ./
RUN pip install --no-cache-dir .

RUN python -c "import bodhan_mcp; print('bodhan_mcp import OK')" && \
    python -m bodhan_mcp --help > /dev/null || python -c "from bodhan_mcp.server import mcp; print('MCP server OK')"

CMD ["python", "-m", "bodhan_mcp"]