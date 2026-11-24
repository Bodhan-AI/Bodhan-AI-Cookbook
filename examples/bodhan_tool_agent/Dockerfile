FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

# Default: run the MCP server over stdio. Override to run the agent:
#   docker run --env-file .env -v $PWD/inputs:/in -v $PWD/out:/out IMAGE \
#     python -m bodhan_agent --task "..." --files /in --out /out
CMD ["python", "-m", "bodhan_mcp"]
