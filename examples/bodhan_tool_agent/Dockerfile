FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md .env.example Makefile ./
COPY src/ ./src/
COPY tests/ ./tests/

RUN pip install --no-cache-dir -e ".[dev]"

ENTRYPOINT ["python", "-m", "bodhan_agent"]
