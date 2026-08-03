# Dockerfile for 24/7 Cloud Deployment
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy workspace code
COPY . .

# Run the 24/7 silent auto-scanner
CMD ["python3", "models/auto_scanner.py"]
