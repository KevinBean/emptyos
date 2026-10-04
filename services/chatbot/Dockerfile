FROM python:3.12-slim

WORKDIR /app

# Install runtime deps directly — pyproject is informational; we pin here.
RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.32" \
    "httpx>=0.27" \
    "openai>=1.50"

# Copy the service into a package dir so relative imports (from .config import ...)
# resolve; /app is on sys.path so `chatbot` is importable.
COPY services/chatbot /app/chatbot
COPY apps/public/standard/publish/static/chatbot-widget.js /app/chatbot/widget/chatbot-widget.js

# SQLite ledger lives in /data (mounted volume in compose).
RUN mkdir -p /data
ENV CHATBOT_DATA_DIR=/data
ENV CHATBOT_SITES_PATH=/app/chatbot/sites.toml

EXPOSE 8000
CMD ["uvicorn", "chatbot.main:app", "--host", "0.0.0.0", "--port", "8000"]
