FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Persistent data
RUN mkdir -p /data
ENV DATA_DIR=/data
ENV PYTHONUNBUFFERED=1

WORKDIR /app/backend
EXPOSE 8000

# Railway injeta $PORT — main.py já respeita
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
