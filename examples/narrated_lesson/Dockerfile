# Narrated lesson CLI — Python + ffmpeg for Speak MP3 / concat / loudnorm.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

# Pass keys via --env-file or -e; mount input/output volumes at runtime.
ENTRYPOINT ["python", "-m", "bodhan_lesson"]
