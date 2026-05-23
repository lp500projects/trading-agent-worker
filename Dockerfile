     1|# Railway deployment — persistent Python worker
     2|FROM python:3.12-slim
     3|
     4|WORKDIR /app
     5|
     6|# Install system deps
     7|RUN apt-get update && apt-get install -y --no-install-recommends \
     8|    curl \
     9|    && rm -rf /var/lib/apt/lists/*
    10|
    11|# Copy and install Python dependencies
    12|COPY pyproject.toml .
    13|RUN pip install --no-cache-dir ccxt pyyaml python-dotenv
    14|
    15|# Copy application files
    16|COPY goal.yaml .
    17|COPY main.py .
    18|COPY loop.py .
    19|COPY reflect.py .
    20|COPY score.py .
    21|COPY adapters/ adapters/
    22|COPY state/ state/
    23|
    24|# Environment
    25|ENV STATE_DIR=/app/state
    26|ENV GOAL_PATH=/app/goal.yaml
    27|ENV PYTHONUNBUFFERED=1
    28|
    29|# Persistent volume mount point for state files
    30|VOLUME ["/app/state"]
    31|
    32|CMD ["python", "main.py"]
    33|