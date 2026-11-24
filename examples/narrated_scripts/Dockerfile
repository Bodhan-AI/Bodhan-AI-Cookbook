FROM python:3.12.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg make \
    && apt-get clean \
    && find /var/lib/apt/lists -type f -delete

WORKDIR /recipe
COPY Makefile pyproject.toml requirements*.txt ./
COPY src/ ./src/
RUN make setup
COPY docs/ ./docs/
COPY tests/ ./tests/
COPY tools/ ./tools/
COPY README.md .env.example ./
COPY sample_data/.gitkeep ./sample_data/.gitkeep
COPY outputs/.gitkeep ./outputs/.gitkeep

ENV PATH="/recipe/.venv/bin:$PATH" BODHAN_BACKEND=hosted
ENTRYPOINT ["python", "-m", "bodhan_lesson"]
