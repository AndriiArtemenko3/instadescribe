# Archived InstaDescribe study backend image. The unauthenticated Flask server
# now binds to loopback and must not be published or reactivated. It remains
# buildable only for local evidence/rollback inspection.
FROM python:3.12-slim

# ffmpeg is required for the TTS mix / eyes-closed preview render.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY modular_pipeline/requirements-server.txt ./requirements-server.txt
RUN pip install --no-cache-dir -r requirements-server.txt

# Server code + built frontend + clip data/videos.
COPY modular_pipeline ./modular_pipeline
COPY App/dist ./App/dist
COPY App/public ./App/public

ENV PORT=8765
WORKDIR /app/modular_pipeline
CMD ["python", "server.py"]
