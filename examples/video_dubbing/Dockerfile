FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 libfribidi0 fonts-noto-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
RUN pip install --no-cache-dir --no-deps -e .
ENTRYPOINT ["python", "-m", "bodhan_dub"]
# CLI:  docker run --env-file .env -v $PWD:/data IMAGE --input /data/video.mp4 --langs hi,ta,bn --out /data/out
# Web:  docker run --env-file .env -p 8000:8000 --entrypoint python IMAGE -m bodhan_dub.web --host 0.0.0.0
