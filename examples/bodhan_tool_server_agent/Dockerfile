FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY src ./src
RUN uv sync --frozen --no-dev && chown -R app:app /app
ENV PATH="/app/.venv/bin:$PATH"
USER app
# Mount inputs and outputs: docker run --env-file .env -v $PWD/in:/in -v $PWD/out:/out bodhan python -m bodhan_agent --task "..." --files /in --out /out
CMD ["python", "-m", "bodhan_mcp"]
