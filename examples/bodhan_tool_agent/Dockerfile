FROM python:3.14.3

WORKDIR /app
COPY requirements.txt pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .
COPY tests ./tests
COPY sample_data ./sample_data

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "bodhan_mcp"]