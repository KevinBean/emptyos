# syntax=docker/dockerfile:1.7

# --- EmptyOS container ---
# Builds a portable EmptyOS image that runs the same code as `eos start` locally.
#
# Build:
#   docker build -t emptyos:latest .
#
# Run (minimum):
#   docker run -d -p 9000:9000 \
#       -v /path/to/your/vault:/vault \
#       -v $(pwd)/emptyos.toml:/app/emptyos.toml \
#       emptyos:latest
#
# Run (public mode with inline token):
#   docker run -d -p 9000:9000 \
#       -v /path/to/your/vault:/vault \
#       -v $(pwd)/emptyos.toml:/app/emptyos.toml \
#       -e EOS_NETWORK_AUTH_TOKEN=your-long-random-token \
#       emptyos:latest

FROM python:3.11-slim AS base

# System deps kept minimal — add ripgrep for the grep_search provider, git for
# release tooling that shells out.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ripgrep \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project metadata first so deps cache separately from code
COPY pyproject.toml README.md ./

# Copy source — everything the package needs to install and run
COPY emptyos/ ./emptyos/
COPY apps/ ./apps/
COPY plugins/ ./plugins/
COPY engines/ ./engines/
COPY docs/ ./docs/
COPY scripts/ ./scripts/
COPY release.toml ./release.toml
COPY emptyos.example.toml ./emptyos.example.toml

# Install EmptyOS and its runtime dependencies
RUN pip install --no-cache-dir -e .

# Default config location inside the container. Mount your real config over this.
ENV EOS_CONFIG=/app/emptyos.toml

# Vault mount point — map your host vault here.
VOLUME ["/vault"]

# Runtime state (syslog, events db, app state). Mount to persist across restarts.
VOLUME ["/app/data"]

EXPOSE 9000

# Start the daemon. The `network.mode = "public"` + auth_token requirement is
# enforced in Python — the container refuses to start in public mode without a
# token set (via emptyos.toml or EOS_NETWORK_AUTH_TOKEN env var).
CMD ["python", "-m", "emptyos", "start"]
