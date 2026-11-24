# Reproducible runtime with system ffmpeg (the primary ffmpeg path; imageio-ffmpeg
# is only a local-dev fallback). Build: docker build -t bodhan-dub .
# Run:   docker run --rm --env-file .env -v "$PWD":/work bodhan-dub \
#          --input /work/video.mp4 --langs hi,ta,bn --out /work/out
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENTRYPOINT ["python", "-m", "bodhan_dub"]
CMD ["--help"]
