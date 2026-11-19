FROM python:3.11-slim

# Install system dependencies including FFmpeg for audio processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pinned python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source and recipe files
COPY src/ src/
COPY pyproject.toml .
COPY README.md .
COPY sample_data/ sample_data/

# Install local package in editable mode
RUN pip install --no-cache-dir -e .

# Entrypoint runs the CLI pipeline
ENTRYPOINT ["python", "-m", "bodhan_lesson"]
CMD ["--help"]
