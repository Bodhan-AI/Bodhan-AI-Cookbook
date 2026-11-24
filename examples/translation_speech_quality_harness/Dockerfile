# Reproducible run of the harness: python -m bodhan_eval inside a clean Linux image.
#   docker build -t bodhan-eval .
#   docker run --rm --env-file .env -v "$PWD/report:/app/report" bodhan-eval \
#     --dataset data/demo.jsonl --langs hi,ta,bn --out report
FROM python:3.11.11-slim

# ffmpeg converts audio that is not 16-bit mono WAV; everything else is pure Python.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

ENTRYPOINT ["python", "-m", "bodhan_eval"]
CMD ["--help"]
