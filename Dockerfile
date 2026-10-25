FROM python:3.12-slim

# 1. Environment variables for Python in Docker
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# 2. System dependencies for PaddleOCR, OpenCV, and PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 3. Install Python dependencies (layer cached)
COPY backend/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy application source code
COPY . .

# 5. Expose application port
EXPOSE 8000

# 6. Default command to start the backend server
CMD ["python", "backend/run_server.py"]
