FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt
COPY tests ./tests
COPY demo ./demo
# Agent:   docker run --env-file .env -v "$PWD/in:/in" -v "$PWD/out:/out" bodhan-tool-agent --task "..." --files /in --out /out
# Server:  docker run -i --env-file .env --entrypoint python bodhan-tool-agent -m bodhan_mcp
ENTRYPOINT ["python", "-m", "bodhan_agent"]
