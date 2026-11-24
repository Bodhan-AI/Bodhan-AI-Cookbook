# Reproducible environment for the harness. ffmpeg is the one system dependency (for audio
# chunking and silence detection); everything else is pip-installed.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps -e .

# Default: run the offline demo so `docker run <image>` proves the install works with no keys.
CMD ["python", "-m", "bodhan_eval", "--dataset", "data/tiny10.jsonl", \
     "--langs", "hi,ta,bn", "--out", "report", "--fake", "--no-speech"]
