FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
COPY docs ./docs

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps -e .

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "bodhan_agent"]
