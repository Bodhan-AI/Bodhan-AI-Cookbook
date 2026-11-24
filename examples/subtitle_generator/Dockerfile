# The whole app in one image: the web app is built, then served by the backend on the same port.
# Simplest: `docker compose up --build`, then open http://localhost:8000 (see README "Run with Docker").
# The normal setup (backend/venv + npm, or `npm start`) doesn't use this file.

# 1. Build the web app
FROM node:22-alpine AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2. The backend, serving the built web app at /
FROM python:3.12-slim
WORKDIR /app/backend
COPY backend/requirements.txt .
# FFmpeg comes from pip (imageio-ffmpeg) here too, as in the normal setup
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app app
COPY --from=web /src/frontend/dist /app/frontend/dist
ENV WEB_DIST=/app/frontend/dist \
    PYTHONUNBUFFERED=1
# Run as a normal user; the job history (SQLite) lives in /app/backend/data, a volume in docker-compose.yml
RUN useradd --create-home app && mkdir -p data && chown app data
USER app
EXPOSE 8000
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
