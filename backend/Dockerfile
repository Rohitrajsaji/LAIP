FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv
FROM python:3.12.15-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home laip && mkdir -p /var/lib/laip/artifacts && chown -R laip:laip /var/lib/laip /app && chmod 700 /var/lib/laip/artifacts
USER laip
CMD ["uvicorn", "laip.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
