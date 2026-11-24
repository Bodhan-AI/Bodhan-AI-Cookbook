FROM python:3.12-slim

# libsndfile is bundled in the soundfile wheel; no ffmpeg or other system packages are needed.
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENTRYPOINT ["python", "-m", "bodhan_eval"]
