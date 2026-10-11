FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends util-linux \
    && rm -rf /var/lib/apt/lists/*

# Antideploy runs one container. Each curated NumPy environment gets its own
# interpreter and an unprivileged worker process inside that container.
RUN python -m venv /opt/worker-old \
    && /opt/worker-old/bin/pip install --no-cache-dir numpy==1.26.4 \
    && python -m venv /opt/worker-new \
    && /opt/worker-new/bin/pip install --no-cache-dir numpy==2.0.2 \
    && useradd --system --create-home workerold \
    && useradd --system --create-home workernew \
    && useradd --system --create-home appuser

COPY pyproject.toml ./
COPY app/ ./app/
COPY runner/ ./runner/
COPY fixture/project/ ./fixture/project/
COPY start-antideploy.sh ./start-antideploy.sh
RUN pip install --no-cache-dir . \
    && chmod 755 /app/start-antideploy.sh \
    && chmod -R a+rX /app

ENV RUNNER_MODE=worker_http \
    AUTH_ALLOWED_ORIGIN=https://reprodelta-lab.antideploy.app \
    WORKER_OLD_URL=http://127.0.0.1:9001/run \
    WORKER_NEW_URL=http://127.0.0.1:9002/run \
    INVESTIGATION_STORE=postgres \
    FIXTURE_DIR=/app/fixture/project

EXPOSE 8000
CMD ["/app/start-antideploy.sh"]
