FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install --no-cache-dir -e .

# Keys come from the environment at run time, never from the image.
ENTRYPOINT ["python", "-m", "bodhan_agent"]
CMD ["--help"]
