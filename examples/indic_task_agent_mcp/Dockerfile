FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install --no-cache-dir --no-deps -e .

# Keys come from --env-file .env at run time; nothing secret is baked into the image.
ENV BODHAN_CACHE_DIR=/app/.bodhan_cache
CMD ["python", "-m", "bodhan_agent", "--help"]
