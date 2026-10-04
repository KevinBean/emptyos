FROM python:3.12-slim

# Build deps for librosa + soundfile (libsndfile) and a CPU-only torch.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 ffmpeg git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# CPU wheels for torch keep the image small. GPU users can layer on top.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV PRONOUNCE_API_HOST=0.0.0.0
ENV PRONOUNCE_API_PORT=8603
# Mount a volume here to persist the ~1.2GB model across container restarts.
ENV PRONOUNCE_MODEL_DIR=/var/lib/pronounce-models
VOLUME ["/var/lib/pronounce-models"]

EXPOSE 8603
CMD ["python", "server.py"]
