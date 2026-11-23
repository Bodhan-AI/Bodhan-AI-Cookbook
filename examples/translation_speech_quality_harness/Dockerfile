# Reproducible runtime: python + ffmpeg (needed only for non-WAV audio input).
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Keys come from the environment at run time, e.g. `docker run --env-file .env -v $PWD/outputs:/app/outputs ...`
ENTRYPOINT ["python", "-m", "bodhan_eval"]
CMD ["--help"]
