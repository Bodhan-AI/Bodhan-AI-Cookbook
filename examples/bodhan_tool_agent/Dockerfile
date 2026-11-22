FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .
COPY . .
ENV BODHAN_CACHE_DIR=/app/.bodhan_cache
# agent:  docker run --env-file .env -v $PWD/inputs:/in -v $PWD/out:/out IMG python -m bodhan_agent --task "..." --files /in --out /out
# server: docker run -i --env-file .env IMG python -m bodhan_mcp
CMD ["python", "-m", "bodhan_mcp"]
