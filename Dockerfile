# Use Python 3.11 slim as base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH /app/src

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy project files
COPY . .

# Expose port
EXPOSE 8000

# Set entrypoint to run the application
CMD ["uvicorn", "infra_rag.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
