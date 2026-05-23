     1|# Railway deployment — persistent Python worker
     2|FROM python:3.12-slim
     3|
     4|WORKDIR /app
     5|
     6|# Install system build deps (ccxt needs gcc for its C extensions)
     7|RUN apt-get update && apt-get install -y --no-install-recommends \
     8|    gcc \
     9|    g++ \
    10|    && rm -rf /var/lib/apt/lists/*
    11|
    12|# Copy and install Python dependencies
    13|COPY pyproject.toml .
    14|RUN pip install --no-cache-dir ccxt pyyaml python-dotenv
    15|
    16|# Clean up build deps to keep image small
    17|RUN apt-get remove -y gcc g++ && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
    18|
    19|# Copy application files
    20|COPY goal.yaml .
    21|COPY main.py .
    22|COPY loop.py .
    23|COPY reflect.py .
    24|COPY score.py .
    25|COPY adapters/ adapters/
    26|COPY state/ state/
    27|
    28|# Environment
    29|ENV STATE_DIR=/app/state
    30|ENV GOAL_PATH=/app/goal.yaml
    31|ENV PYTHONUNBUFFERED=1
    32|
    33|# Attach a Railway Volume at /app/state via the dashboard (Settings → Volumes)
    34|CMD ["python", "main.py"]
    35|