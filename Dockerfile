# InstaDescribe study backend — single-origin deploy.
# Serves the built SPA (App/dist), per-session data (App/public/data),
# videos (App/public/videos) and the /api endpoints from one process.
#
# Build the frontend FIRST (see build-study.sh), then:
#   docker build -t instadescribe-study .
#   docker run -p 8765:8765 -e OPENAI_API_KEY=sk-... -e STUDY_CORS_ORIGINS="*" instadescribe-study
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
EXPOSE 8765
WORKDIR /app/modular_pipeline
CMD ["python", "server.py"]
