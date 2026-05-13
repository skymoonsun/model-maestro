FROM python:3.11-slim

# Install cloudflared binary for tunnel support
RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates \
    && wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
    && dpkg -i cloudflared-linux-amd64.deb || apt-get install -f -y \
    && rm cloudflared-linux-amd64.deb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip setuptools
RUN pip install --no-cache-dir --timeout 240 --retries 5 -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY docker-entrypoint.sh .

# Copy Alembic files for database migrations
COPY alembic.ini .
COPY alembic/ ./alembic/

# Create directories for data and cache
RUN mkdir -p /app/data /app/cache

# Make scripts executable
RUN chmod +x docker-entrypoint.sh

# Expose port
EXPOSE 8000

# Set entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default command to run the FastAPI server
# --timeout-keep-alive 300: Keep connection alive for streaming
# --limit-concurrency 1000: Allow many concurrent connections
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "300", "--limit-concurrency", "1000"]

