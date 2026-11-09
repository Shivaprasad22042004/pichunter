FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ORT_THREADS=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgl1 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- models are fetched and quantised at build time (source files are 184 MB,
#     GitHub rejects files over 100 MB; int8 versions are 48 MB total) ---
COPY scripts/fetch_models.py scripts/fetch_models.py
RUN mkdir -p models && python scripts/fetch_models.py

COPY app.py .
COPY stats.py .
COPY static/ ./static/
COPY data/ ./data/

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
