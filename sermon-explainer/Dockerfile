FROM python:3.11-slim

# Install ffmpeg (required for audio/video processing)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first so Docker can cache this layer separately
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Render provides the port via the PORT environment variable.
# Shell form (not exec/array form) is required so $PORT actually expands.
CMD uvicorn api:app --host 0.0.0.0 --port $PORT
