FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ARG TESSDATA_BEST_REV=e12c65a915945e4c28e237a9b52bc4a8f39a0cec
ARG TESSDATA_BEST_ENG_SHA256=8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XRAY_FRONTEND_DIST_DIR=/app/frontend-dist

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tesseract-ocr \
    && curl --fail --location --silent --show-error \
        "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/${TESSDATA_BEST_REV}/eng.traineddata" \
        --output /tmp/eng.traineddata \
    && echo "${TESSDATA_BEST_ENG_SHA256}  /tmp/eng.traineddata" | sha256sum --check --strict \
    && install -m 0644 /tmp/eng.traineddata /usr/share/tesseract-ocr/5/tessdata/eng.traineddata \
    && rm /tmp/eng.traineddata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
RUN pip install --no-cache-dir .
COPY --from=frontend /build/frontend/dist /app/frontend-dist

EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
