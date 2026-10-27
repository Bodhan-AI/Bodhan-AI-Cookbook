# Backend + web app in one image. The web build is served by FastAPI on the same origin.
#   docker build -t support-call-summarizer .
#   docker run --env-file .env -p 8000:8000 support-call-summarizer

FROM node:22-alpine AS web
WORKDIR /src
COPY package.json package-lock.json ./
COPY web/package.json web/
RUN npm ci
COPY web/ web/
RUN npm run build

FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app app
COPY backend/cli.py .
COPY --from=web /src/web/dist /app/web/dist
RUN useradd --create-home appuser
USER appuser
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
