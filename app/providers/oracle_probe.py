"""Live synthetic Oracle runner proof; run inside project-worker-oracle.

Run before enabling owner project execution. No repository source is used.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

from app.providers import oracle_docker_runner
from app.providers.e2b_runner import ProjectAdapter

PROBE = b"""def run(data):
    import os
    import socket
    import ssl
    import time
    import numpy as np
    if data['mode'] == 'fail':
        raise RuntimeError('expected probe failure')
    if data['mode'] == 'hang':
        time.sleep(60)
    if data['mode'] == 'overflow':
        print('x' * 200000)
    secret_names = ('E2B_API_KEY', 'NEBIUS_API_KEY', 'DATABASE_URL',
                    'SECRET_KEY', 'PROJECT_SNAPSHOT_KEY',
                    'GITHUB_OAUTH_CLIENT_SECRET', 'GITLAB_OAUTH_CLIENT_SECRET')
    secret_present = any(os.environ.get(name) for name in secret_names)
    docker_socket_present = os.path.exists('/var/run/docker.sock')
    try:
        with socket.create_connection(('1.1.1.1', 443), timeout=2) as raw:
            raw.settimeout(2)
            with ssl.create_default_context().wrap_socket(
                raw, server_hostname='cloudflare-dns.com') as connection:
                connection.sendall(b'HEAD / HTTP/1.1\\r\\nHost: cloudflare-dns.com\\r\\nConnection: close\\r\\n\\r\\n')
                network_open = connection.recv(32).startswith(b'HTTP/')
    except OSError:
        network_open = False
    value = float(np.mean(np.asarray(data['signal'], dtype=np.float32)))
    return {'ratio': value, 'secret_present': secret_present,
            'docker_socket_present': docker_socket_present,
            'network_open': network_open}
"""


def _empty_sandbox_list() -> None:
    listed = oracle_docker_runner._command(
        ["ps", "-aq", "--filter", "label=reprodelta.project-sandbox=1"], timeout=15,
    )
    if listed.returncode != 0 or listed.stdout.strip():
        raise RuntimeError("Oracle project containers remain after the probe")


def _run(
    version: str, mode: str, *, timeout: int = 60,
    should_abort: Callable[[], bool] | None = None,
):
    template_id = oracle_docker_runner._PILOT[version][0]
    lock_digest = hashlib.sha256(f"python==3.12\nnumpy=={version}\n".encode()).hexdigest()
    return oracle_docker_runner.execute_environment(
        files={"probe.py": PROBE}, input_json={"mode": mode, "signal": [0.25, 0.75]},
        adapter=ProjectAdapter("probe", "run"), template_id=template_id,
        expected_numpy=version, expected_lock_sha256=lock_digest,
        timeout_seconds=timeout, should_abort=should_abort,
    )


def main() -> None:
    oracle_docker_runner.preflight()
    for version in ("1.26.4", "2.0.2"):
        result = _run(version, "normal")
        observation = result.output["result"]
        if (observation.get("ratio") != 0.5 or observation.get("secret_present")
                or observation.get("docker_socket_present")
                or observation.get("network_open")):
            raise RuntimeError("Oracle isolation or numerical probe failed")
        _empty_sandbox_list()
        print(f"NumPy {version}: paired image check and offline probe passed; "
              f"container removed after {result.duration_seconds:.2f}s")
    expected_errors = {
        "fail": "Approved adapter failed inside the container",
        "hang": "Docker command timed out",
        "overflow": "Docker command output exceeded its bound",
    }
    for mode, timeout in (("fail", 30), ("hang", 15), ("overflow", 30)):
        started = time.monotonic()
        try:
            _run("1.26.4", mode, timeout=timeout)
        except RuntimeError as exc:
            if expected_errors[mode] not in str(exc):
                raise RuntimeError(f"{mode} probe failed for another reason") from exc
            if mode == "hang" and time.monotonic() - started < 8:
                raise RuntimeError("Timeout probe ended before the adapter could be exercised") from None
            _empty_sandbox_list()
            print(f"{mode}: failure stayed inconclusive and container was removed")
        else:
            raise RuntimeError(f"{mode} probe unexpectedly succeeded")
    started = time.monotonic()
    try:
        _run("1.26.4", "hang", should_abort=lambda: time.monotonic() - started > 2)
    except RuntimeError as exc:
        if "cancelled or its worker lease was lost" not in str(exc):
            raise RuntimeError("Cancellation probe failed for another reason") from exc
        if time.monotonic() - started > 8:
            raise RuntimeError("Oracle adapter cancellation was not prompt") from None
        _empty_sandbox_list()
        print("cancel: running adapter interrupted and container removed")
    else:
        raise RuntimeError("Cancellation probe unexpectedly succeeded")


if __name__ == "__main__":
    main()
