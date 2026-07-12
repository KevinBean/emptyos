# syntax=docker/dockerfile:1.7

# --- EmptyOS container ---
# Builds a portable EmptyOS image that runs the same code as `eos start` locally.
#
# Build:
#   docker build -t emptyos:latest .
#
# Run (one-liner, no pre-existing config — entrypoint bootstraps first boot):
#   docker run -d -p 9000:9000 \
#       -v $HOME/emptyos-vault:/vault \
#       ghcr.io/kevinbean/emptyos:latest
#
# Run (with your own config):
#   docker run -d -p 9000:9000 \
#       -v /path/to/your/vault:/vault \
#       -v $(pwd)/emptyos.toml:/app/emptyos.toml \
#       emptyos:latest
#
# Run (public mode with inline token):
#   docker run -d -p 9000:9000 \
#       -v /path/to/your/vault:/vault \
#       -e EOS_NETWORK_MODE=public \
#       -e EOS_NETWORK_AUTH_TOKEN=your-long-random-token \
#       emptyos:latest

FROM python:3.12-slim AS base

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

# First-boot bootstrap — writes a default config + auth token + PARA-seeded
# vault when those don't already exist. Idempotent on restart.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Default config location inside the container. Mount your real config over
# this to skip the auto-generated one.
ENV EOS_CONFIG=/app/emptyos.toml

# Vault mount point — map your host vault here.
VOLUME ["/vault"]

# Runtime state (syslog, events db, app state). Mount to persist across restarts.
VOLUME ["/app/data"]

EXPOSE 9000

# The entrypoint bootstraps first-boot state (config, token, vault skeleton)
# then exec's CMD. The `network.mode = "public"` + auth_token requirement is
# enforced in Python — the container refuses to start in public mode without
# a token set (via emptyos.toml or EOS_NETWORK_AUTH_TOKEN env var).
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "emptyos", "start"]
