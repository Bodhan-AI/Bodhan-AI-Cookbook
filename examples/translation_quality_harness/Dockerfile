# Reproducible runtime: docker build -t bodhan-eval . && docker run --rm -v "$PWD:/work" bodhan-eval --dataset data/demo.jsonl --langs hi,ta,bn --out /work/report --offline --judge off --max-cost-inr 0 --cache-dir .demo_cache
FROM python:3.11.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt

COPY data ./data
COPY tests ./tests
COPY scripts ./scripts

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 BODHAN_EVAL_CACHE=/work/.bodhan_cache
WORKDIR /work
ENTRYPOINT ["python", "-m", "bodhan_eval"]
