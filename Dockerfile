# ODC v4 — production Dockerfile
# Multi-stage build for small final image.

# === Stage 1: builder ===
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    git-lfs \
    && rm -rf /var/lib/apt/lists/*

# Copy only what's needed for install
COPY requirements.txt setup.py pyproject.toml ./
COPY odc ./odc

# Install Python deps
RUN pip install --no-cache-dir --upgrade pip wheel \
    && pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt \
    && pip wheel --no-cache-dir --wheel-dir=/wheels .

# === Stage 2: runtime ===
FROM python:3.11-slim AS runtime

LABEL maintainer="Grupo AN Development"
LABEL description="ODC v4 — Open Digital Companion, self-improving LLM agent"
LABEL version="4.0.0"

WORKDIR /app

# Runtime system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-lfs \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install

# Install Playwright browser (chromium only)
RUN pip install playwright==1.40.0 && \
    python -m playwright install chromium --with-deps && \
    playwright install-deps chromium

# Copy wheels from builder
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels odc-v4 \
    && rm -rf /wheels

# Copy entrypoint
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Create non-root user
RUN useradd -m -u 1000 odc && \
    mkdir -p /app/.odc_data /app/logs && \
    chown -R odc:odc /app

USER odc

ENV ODC_DATA_DIR=/app/.odc_data \
    ODC_HOST=0.0.0.0 \
    ODC_PORT=8000 \
    ODC_LOG_LEVEL=INFO

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["server"]
