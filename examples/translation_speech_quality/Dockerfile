FROM python:3.12-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /recipe
COPY pyproject.toml requirements.txt ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir .

WORKDIR /work
ENTRYPOINT ["python", "-m", "bodhan_eval"]
