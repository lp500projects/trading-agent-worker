# Railway deployment — persistent Python worker
FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY goal.yaml .
COPY main.py .
COPY loop.py .
COPY reflect.py .
COPY score.py .
COPY adapters/ adapters/
COPY state/ state/

# Install Python dependencies
RUN uv pip install --system -e .

# Railway provides PORT, but we don't run an HTTP server
# The worker is a long-running process
ENV STATE_DIR=/app/state
ENV GOAL_PATH=/app/goal.yaml
ENV PYTHONUNBUFFERED=1

# Persistent volume mount point for state files
VOLUME ["/app/state"]

CMD ["python", "main.py"]
