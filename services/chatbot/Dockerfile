FROM python:3.12-slim

WORKDIR /app

# Install runtime deps directly — pyproject is informational; we pin here.
RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.32" \
    "httpx>=0.27" \
    "openai>=1.50"

COPY . /app

# SQLite ledger lives in /data (mounted volume in compose).
RUN mkdir -p /data
ENV CHATBOT_DATA_DIR=/data
ENV CHATBOT_SITES_PATH=/app/sites.toml

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
