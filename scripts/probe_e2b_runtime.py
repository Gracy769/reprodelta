"""Run a synthetic credential and network isolation probe in one pilot VM.

The firewall may accept TCP connect before denying egress. An application
response, rather than a successful connect(), is required to prove reachability.
"""

from __future__ import annotations

import os

from verify_e2b_templates import template_credentials

from app.providers.e2b_runner import ProjectAdapter, execute_environment

PROBE = b"""def run(data):
    import os
    import socket
    import ssl
    names = ('E2B_API_KEY', 'NEBIUS_API_KEY', 'DATABASE_URL',
             'GITHUB_OAUTH_CLIENT_SECRET', 'GITLAB_OAUTH_CLIENT_SECRET')
    secret_present = any(os.environ.get(name) for name in names)
    try:
        with socket.create_connection(('1.1.1.1', 443), timeout=3) as raw:
            raw.settimeout(3)
            with ssl.create_default_context().wrap_socket(
                raw, server_hostname='cloudflare-dns.com') as connection:
                connection.sendall(b'HEAD / HTTP/1.1\\r\\nHost: cloudflare-dns.com\\r\\nConnection: close\\r\\n\\r\\n')
                network_open = connection.recv(32).startswith(b'HTTP/')
    except OSError:
        network_open = False
    return {'ratio': 0.0, 'secret_present': secret_present,
            'network_open': network_open}
"""


def main() -> None:
    if not os.environ.get("E2B_API_KEY"):
        raise SystemExit("E2B_API_KEY is required for the live probe")
    version = "1.26.4"
    template_id, digest = template_credentials(version)
    result = execute_environment(
        files={"probe.py": PROBE}, input_json={},
        adapter=ProjectAdapter("probe", "run"),
        template_id=template_id, expected_numpy=version,
        expected_lock_sha256=digest,
        timeout_seconds=90,
    )
    observed = result.output["result"]
    if observed.get("secret_present") or observed.get("network_open"):
        raise SystemExit(
            "Isolation probe failed; do not enable project execution. "
            f"credential_visible={bool(observed.get('secret_present'))}, "
            f"outbound_tcp_open={bool(observed.get('network_open'))}"
        )
    print(
        f"Isolation passed in sandbox {result.sandbox_id}: service credentials absent, "
        f"outbound TCP blocked, cleanup complete ({result.duration_seconds:.2f}s)."
    )


if __name__ == "__main__":
    main()
