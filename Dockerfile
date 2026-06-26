# CCTV Tailgate — container image
FROM python:3.12-slim

# Runtime libraries required by OpenCV.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first so they are cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Application source.
COPY . .

EXPOSE 8080

# Run the web service. The container binds 0.0.0.0 so the published port is
# reachable from the host; docker-compose.yml restricts that publish to
# 127.0.0.1. Ultralytics downloads the YOLO model on first launch if it is not
# already present in the build context.
CMD ["python", "-m", "src.main", "--host", "0.0.0.0", "--port", "8080"]
