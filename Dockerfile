FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src/ /app/

RUN useradd --uid 10001 --home-dir /app --shell /usr/sbin/nologin svc-lb-mux \
    && chown -R svc-lb-mux:svc-lb-mux /app
USER 10001

CMD ["kopf", "run", "--verbose", "--standalone", "--all-namespaces", "--liveness=http://0.0.0.0:8081/healthz", "/app/main.py"]
