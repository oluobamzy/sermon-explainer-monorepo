FROM python:3.11-slim

# Install system dependencies.
# - ffmpeg: required for audio/video processing
# - curl/unzip/ca-certificates: required to install deno and yt-dlp binary
RUN apt-get update && apt-get install -y --no-install-recommends \
	ffmpeg \
	curl \
	unzip \
	ca-certificates \
	&& rm -rf /var/lib/apt/lists/*

# Install Deno (required by yt-dlp for JS-based signature/n-challenge solving).
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y

# Install the latest yt-dlp standalone binary and force fetch_source to use it.
ARG YTDLP_VERSION=2026.07.04
RUN curl -L --fail "https://github.com/yt-dlp/yt-dlp/releases/download/${YTDLP_VERSION}/yt-dlp_linux" \
	-o /usr/local/bin/yt-dlp \
	&& chmod +x /usr/local/bin/yt-dlp
ENV YTDLP_COMMAND=/usr/local/bin/yt-dlp
ENV YTDLP_PRIMARY_PLAYER_CLIENT=android
ENV YTDLP_FALLBACK_PLAYER_CLIENTS=web,mweb

WORKDIR /app

# Copy requirements first so Docker can cache this layer separately
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Render provides the port via the PORT environment variable.
# Shell form (not exec/array form) is required so $PORT actually expands.
CMD uvicorn api:app --host 0.0.0.0 --port $PORT
