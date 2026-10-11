"""Small internal HTTP transport for the isolated fixture worker."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from .entrypoint import MAX_INPUT_SIZE, run_fixture
except ImportError:  # direct execution inside the minimal worker image
    from entrypoint import MAX_INPUT_SIZE, run_fixture


class Handler(BaseHTTPRequestHandler):
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
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
