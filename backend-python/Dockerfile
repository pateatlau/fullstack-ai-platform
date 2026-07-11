# Python Backend: uv-based, non-root runtime
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && pip install --no-cache-dir --upgrade pip uv \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install dependencies to a virtual environment
RUN uv sync --no-dev

# Stage 2: Runtime
FROM python:3.12-slim

WORKDIR /app

# Create non-root user
RUN groupadd -g 1001 appuser && useradd -u 1001 -g 1001 appuser

# Install minimal runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/pyproject.toml /app/uv.lock* ./

# Copy application code
COPY app ./app

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

USER appuser

# Activate venv in PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
