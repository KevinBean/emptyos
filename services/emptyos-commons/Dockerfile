FROM python:3.12-slim

WORKDIR /app

# Install the service (web/*.html ships as package-data via pyproject).
COPY pyproject.toml ./
COPY emptyos_commons ./emptyos_commons
RUN pip install --no-cache-dir .

# SQLite store lives in a mounted volume (see docker-compose.yml). No DB server.
ENV COMMONS_DATABASE_URL=sqlite:///data/commons.db \
    COMMONS_AUTH_PROVIDER=password \
    COMMONS_SESSION_SECURE=true
RUN mkdir -p /app/data

EXPOSE 9300
CMD ["uvicorn", "emptyos_commons.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "9300"]
