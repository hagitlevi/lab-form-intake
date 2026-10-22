FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-heb libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# 512 MB / 0.5 CPU: give big image buffers back to the OS, one OCR thread
ENV MALLOC_ARENA_MAX=2 MALLOC_MMAP_THRESHOLD_=131072 MALLOC_TRIM_THRESHOLD_=131072 OMP_THREAD_LIMIT=1 LOCAL_WORKERS=1 AI_WORKERS=2
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY sql/ sql/
COPY --from=web /web/dist frontend/dist
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-10000}
