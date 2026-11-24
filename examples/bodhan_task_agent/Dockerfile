FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BODHAN_WORKSPACE=/workspace
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir -e .
RUN mkdir -p /workspace
# Override command to run python -m bodhan_agent ... instead.
CMD ["python", "-m", "bodhan_mcp"]
