FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
# Pass keys with --env-file .env. Default: the MCP server on stdio.
CMD ["python", "-m", "bodhan_mcp"]
