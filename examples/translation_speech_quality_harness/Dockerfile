# Reproducible run environment for the quality harness.
#   docker build -t bodhan-eval .
#   docker run --rm --env-file .env -v "$PWD/report:/app/report" bodhan-eval \
#     --dataset sample_data/demo.jsonl --langs hi,ta,bn --out ./report --generate --max-cost-inr 12
# No system packages are needed: the soundfile wheel bundles libsndfile, and no ffmpeg is used.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt

COPY sample_data ./sample_data
COPY scripts ./scripts
COPY tests ./tests

ENTRYPOINT ["python", "-m", "bodhan_eval"]
