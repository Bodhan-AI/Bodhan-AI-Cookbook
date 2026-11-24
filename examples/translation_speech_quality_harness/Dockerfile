FROM python:3.11-slim

# System dependencies: libsndfile1 for soundfile WAV I/O, and ffmpeg documented
# for audio format conversions / external clip inspection.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY sample_data ./sample_data
COPY scripts ./scripts
COPY tests ./tests
RUN pip install --no-cache-dir --no-deps -e .

ENTRYPOINT ["python", "-m", "bodhan_eval"]
CMD ["--dataset", "sample_data/demo.jsonl", "--langs", "hi,ta,bn", "--out", "outputs/demo_report", "--calibration", "sample_data/demo.jsonl", "--max-cost-inr", "0"]
