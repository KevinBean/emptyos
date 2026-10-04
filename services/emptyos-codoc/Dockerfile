FROM python:3.12-slim

WORKDIR /app

# Install the service (web/*.html ships as package-data via pyproject; pycrdt
# pulls a prebuilt wheel — no Rust toolchain needed).
COPY pyproject.toml ./
COPY emptyos_codoc ./emptyos_codoc
RUN pip install --no-cache-dir .

# Live-doc state (metadata + last snapshot) persists in a mounted volume.
ENV CODOC_DB=/app/data/codoc.db
RUN mkdir -p /app/data

EXPOSE 9400
CMD ["uvicorn", "emptyos_codoc.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "9400"]
