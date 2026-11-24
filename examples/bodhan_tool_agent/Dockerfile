FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
# Keys and LLM settings come from the environment (docker run --env-file .env ...)
ENTRYPOINT ["python", "-m"]
CMD ["bodhan_mcp"]
