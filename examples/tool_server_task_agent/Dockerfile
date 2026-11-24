# Build:  docker build -t bodhan-tool-agent .
#
# Agent:  docker run --rm --env-file .env -v "$PWD/inputs:/data/in:ro" -v "$PWD/out:/data/out" bodhan-tool-agent \
#           --task "Translate this worksheet into Marathi and keep the table" --files /data/in --out /data/out
# Server: docker run -i --rm --env-file .env --entrypoint python bodhan-tool-agent -m bodhan_mcp
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

ENV PYTHONUNBUFFERED=1 \
    BODHAN_CACHE_DIR=/cache
VOLUME ["/cache"]

# No secrets are baked in: pass them with --env-file or -e.
ENTRYPOINT ["python", "-m", "bodhan_agent"]
