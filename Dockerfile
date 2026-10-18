# ---- 1. build the React (Vite) UI
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. Python API (FastAPI + OR-Tools + Gemini) serving the UI
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 \
    AGRISUS_DATA_DIR=/app/data/app AGRISUS_GEO_PATH=/app/data/geo/districts_india.geojson \
    AGRISUS_MODEL_DIR=/app/models AGRISUS_STATIC_DIR=/app/static
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/models ./models
COPY data/processed/app ./data/app
COPY data/processed/geo/districts_india.geojson ./data/geo/districts_india.geojson
COPY --from=web /web/dist ./static
RUN useradd -m agrisus && chown -R agrisus /app
USER agrisus
EXPOSE 8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 75
