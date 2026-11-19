# 1. The web app (frontend/), built once; only its dist/ goes into the final image, and the backend
#    serves it next to the API (backend/api/web_app.py).
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

# 2. Environment variables for Python in Docker
ARG APP_VERSION=1.0.0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_VERSION=${APP_VERSION}

WORKDIR /app

# 3. System dependencies for PaddleOCR, OpenCV, and PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 4. Install Python dependencies (layer cached)
COPY backend/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy application source code, then the built web app
COPY . .
COPY --from=web /web/dist /app/frontend/dist

# 6. Expose application port
EXPOSE 8000

# 7. Default command to start the backend server
CMD ["python", "backend/run_server.py"]
