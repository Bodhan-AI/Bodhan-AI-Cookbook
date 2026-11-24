FROM python:3.12.6-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src
COPY tests ./tests
COPY data ./data
RUN python -m pip install --no-cache-dir . && python -m unittest discover -s tests -v
ENTRYPOINT ["python", "-m", "bodhan_eval"]
