#!/bin/sh
set -eu

# The worker processes receive only the fixed fixture and their NumPy version.
# Nebius and Postgres credentials stay in the API process environment.
runuser -u workerold -- env -i \
  PATH=/opt/worker-old/bin:/usr/local/bin:/usr/bin:/bin \
  WORKER_HOST=127.0.0.1 WORKER_PORT=9001 \
  FIXTURE_DIR=/app/fixture/project EXPECTED_RUNNER_KIND=old \
  /opt/worker-old/bin/python /app/runner/server.py &
old_pid=$!

runuser -u workernew -- env -i \
  PATH=/opt/worker-new/bin:/usr/local/bin:/usr/bin:/bin \
  WORKER_HOST=127.0.0.1 WORKER_PORT=9002 \
  FIXTURE_DIR=/app/fixture/project EXPECTED_RUNNER_KIND=new \
  /opt/worker-new/bin/python /app/runner/server.py &
new_pid=$!

trap 'kill "$old_pid" "$new_pid" 2>/dev/null || true' EXIT INT TERM

python - <<'PY'
import socket
import time

for port in (9001, 9002):
    for attempt in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        raise SystemExit(f"Worker on port {port} failed to start")
PY

runuser -u appuser -- uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
api_pid=$!
wait "$api_pid"
