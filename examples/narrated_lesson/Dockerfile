# Narrated lesson CLI — Python + ffmpeg for Speak MP3 / concat / loudnorm.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
ENV PYTHONPATH=/app/src

# Pass keys via --env-file or -e; mount input/output volumes at runtime.
ENTRYPOINT ["python", "-m", "bodhan_lesson"]
