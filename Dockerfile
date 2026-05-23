# Railway deployment — persistent Python worker
FROM python:3.12-slim

WORKDIR /app

# Install system build deps (ccxt needs gcc for its C extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir ccxt pyyaml python-dotenv

# Clean up build deps to keep image small
RUN apt-get remove -y gcc g++ && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Copy application files
COPY goal.yaml .
COPY main.py .
COPY loop.py .
COPY reflect.py .
COPY score.py .
COPY adapters/ adapters/
COPY state/ state/

# Environment
ENV STATE_DIR=/app/state
ENV GOAL_PATH=/app/goal.yaml
ENV PYTHONUNBUFFERED=1

# Attach a Railway Volume at /app/state via the dashboard (Settings → Volumes)
CMD ["python", "main.py"]