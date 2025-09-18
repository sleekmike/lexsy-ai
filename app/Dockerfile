# Lexsy SAFE Filler - Backend API (FastAPI) for Fly.io
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data/sessions \
    PORT=8000

WORKDIR /app

# System deps (gcc often needed for uvicorn[standard] wheels on slim)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cache-friendly)
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r /app/requirements.txt

# Copy app
COPY . /app

# Non-root user
RUN adduser --disabled-password --gecos '' appuser \
 && chown -R appuser:appuser /app \
 && mkdir -p /data/sessions \
 && chown -R appuser:appuser /data/sessions
USER appuser

EXPOSE 8000

# NOTE: Fly will perform health checks via fly.toml. No Docker HEALTHCHECK needed.
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000"]
