# Unmodified Ente Museum image; source and license are linked in THIRD_PARTY_NOTICES.md.
FROM ghcr.io/ente/server@sha256:3511ff25db3bf950abeb3983928eeadeed7f53b690ee94704c14be72fa719b1d AS ente
FROM ghcr.io/astral-sh/uv:0.12.9 AS uv
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY litellm_memory ./litellm_memory
COPY moegent ./moegent
COPY static ./static
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY --from=ente /museum /opt/ente/museum
COPY --from=ente /configurations /opt/ente/configurations
COPY --from=ente /migrations /opt/ente/migrations
COPY --from=ente /mail-templates /opt/ente/mail-templates
COPY --from=ente /web-templates /opt/ente/web-templates
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 STATE_DIR=/data PORT=8000
EXPOSE 8000
CMD ["python", "-m", "litellm_memory.boot"]
