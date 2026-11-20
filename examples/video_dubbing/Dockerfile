# Video dubbing with Bodhan's Indic models.
#   docker build -t bodhan-dub .
#   docker run --rm --env-file .env -v "$PWD/data:/data" bodhan-dub --input /data/video.mp4 --langs hi,ta,bn --out /data/out
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt

# Bake the Demucs model into the image so runs need no download (Silero VAD ships inside its package).
RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs')"

COPY tests ./tests
ENTRYPOINT ["python", "-m", "bodhan_dub"]
