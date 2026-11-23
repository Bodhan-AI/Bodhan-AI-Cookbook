FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /recipe
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir pip==25.3 setuptools==80.9.0 \
    && python -m pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-build-isolation --no-deps .
ENTRYPOINT ["python", "-m", "bodhan_dub"]
