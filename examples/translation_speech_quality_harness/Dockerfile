FROM python:3.11-slim

# ffmpeg is only needed for non-WAV audio inputs (mp3/flac/ogg); WAV is decoded natively.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir --no-deps -e .
COPY data ./data
COPY tests ./tests

# Pass keys at run time: docker run --env-file .env -v $PWD/report:/app/report bodhan-eval --dataset data/demo.jsonl --langs hi,ta,bn --out report
ENTRYPOINT ["python", "-m", "bodhan_eval"]
