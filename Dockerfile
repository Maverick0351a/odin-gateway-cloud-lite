ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1 \
	PYTHONPATH=/app

WORKDIR /app

# Install only runtime dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
	&& useradd -u 1001 -m runner \
	&& mkdir -p /app/app /app/scripts \
	&& chown -R runner:runner /app

# Copy application source late to maximize cache hits for deps.
COPY app ./app
COPY scripts ./scripts
COPY README.md COPILOT.md ruff.toml ./

USER runner

EXPOSE 8080

# Lightweight healthcheck using Python's stdlib (Cloud Run will also send its own checks)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD ["python","-c","import os,urllib.request,sys;port=os.getenv('PORT','8080');url=f'http://127.0.0.1:{port}/healthz';\n\ntry:\n r=urllib.request.urlopen(url,timeout=2);sys.exit(0 if r.status==200 else 1)\nexcept Exception: sys.exit(1)"]

# Respect Cloud Run provided PORT and optional UVICORN_WORKERS.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers ${UVICORN_WORKERS:-1}"]
