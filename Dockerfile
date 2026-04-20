FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Create data directory
RUN mkdir -p /data

ENV DATA_DIR=/data
ENV PORT=8000

WORKDIR /app/backend

EXPOSE 8000

CMD ["python", "main.py"]
