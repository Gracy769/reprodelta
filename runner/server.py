"""Small internal HTTP transport for the isolated fixture worker."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version

try:
    from .entrypoint import FIXTURE_DIR, MAX_INPUT_SIZE, run_fixture
    from .patching import project_digest, project_sources
except ImportError:  # direct execution inside the minimal worker image
    from entrypoint import FIXTURE_DIR, MAX_INPUT_SIZE, run_fixture
    from patching import project_digest, project_sources


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        payload = json.dumps({"status": "ok", "kind": os.getenv("EXPECTED_RUNNER_KIND"),
                              "numpy_version": version("numpy"),
                              "project_sha256": project_digest(project_sources(FIXTURE_DIR))}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path != "/run":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_INPUT_SIZE:
                self.send_error(413)
                return
            manifest = json.loads(self.rfile.read(length))
            result = run_fixture(manifest)
            payload = json.dumps(result).encode()
            self.send_response(200)
        except (ValueError, KeyError, TypeError) as exc:
            payload = json.dumps({"status": "error", "error": str(exc), "outputs": {}}).encode()
            self.send_response(400)
        except Exception:
            payload = b'{"status":"error","error":"Worker execution failed","outputs":{}}'
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    host = os.environ.get("WORKER_HOST", "0.0.0.0")
    port = int(os.environ.get("WORKER_PORT", "9000"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()
