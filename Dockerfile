# CCTV Tailgate — container image
FROM python:3.12-slim-bookworm

# PYTHONUNBUFFERED: flush stdout/stderr immediately so Docker logs are live.
# PYTHONDONTWRITEBYTECODE: skip .pyc generation; source is read-only in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Runtime libraries required by OpenCV headless wheels (libGL + glib threads).
# No display server is needed in a headless container.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Non-root service account. UID 1000 matches a typical Linux host user so
# bind-mount directories (captures/, logs/, data/) remain writable at runtime.
RUN useradd --uid 1000 --system --no-create-home sentry

WORKDIR /app

# Create the config mount-point as a file so Docker does not auto-create it as
# a directory when the volume is first mounted (config.yaml is excluded from the
# build context via .dockerignore and must be supplied at runtime).
RUN touch /app/config.yaml

# Install Python dependencies first so they are cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Application source.
COPY . .

# Pre-create runtime output dirs (excluded from .dockerignore) and hand
# ownership of the whole workdir to the service account.
RUN mkdir -p captures logs data \
    && chown -R sentry /app

USER sentry

EXPOSE 8080

CMD ["python", "-m", "src.main", "--host", "0.0.0.0", "--port", "8080"]
