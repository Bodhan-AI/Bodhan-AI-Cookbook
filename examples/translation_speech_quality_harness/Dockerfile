FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY examples/translation_speech_quality_harness/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY examples/translation_speech_quality_harness/src /app/src
ENV PYTHONPATH=/app/src PYTHONUTF8=1
ENTRYPOINT ["python", "-m", "bodhan_eval"]
