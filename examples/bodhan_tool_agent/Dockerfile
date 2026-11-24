FROM python:3.11-slim

# System deps: ffmpeg for audio, poppler for PDF rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Non-root user for security best practices
RUN useradd -m -u 1000 appuser

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests

RUN pip install --no-cache-dir -e ".[dev]" && \
    chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["python"]
CMD ["-m", "bodhan_agent", "--help"]
