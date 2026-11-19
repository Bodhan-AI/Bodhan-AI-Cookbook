FROM python:3.11-slim

# ffmpeg: audio conversion, silence detection, joining speech pieces.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Keys come from --env-file at run time; .env is never copied (see .dockerignore).
ENTRYPOINT ["python", "-m"]
CMD ["bodhan_agent", "--help"]
