FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY scripts ./scripts
RUN pip install --no-cache-dir -e . --no-deps
# keys and LLM settings come from the environment / --env-file .env
ENTRYPOINT ["python", "-m"]
CMD ["bodhan_mcp"]
