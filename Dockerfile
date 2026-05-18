FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY scripts/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY scripts/ /app/

RUN useradd --uid 10001 --home-dir /app --shell /usr/sbin/nologin slb-mux \
    && chown -R slb-mux:slb-mux /app
USER 10001

CMD ["kopf", "run", "--verbose", "--standalone", "--all-namespaces", "--liveness=http://0.0.0.0:8081/healthz", "/app/main.py"]
