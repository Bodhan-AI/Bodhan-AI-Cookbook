FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml requirements.txt requirements-dev.txt ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir --no-deps -e .
COPY tests ./tests
COPY demo ./demo
# Agent:  docker run --env-file .env -v "$PWD/inputs:/inputs" -v "$PWD/out:/out" indic-task-agent \
#           python -m bodhan_agent --task "..." --files /inputs --out /out
# Server: docker run -i --env-file .env indic-task-agent python -m bodhan_mcp
CMD ["python", "-m", "bodhan_mcp"]
