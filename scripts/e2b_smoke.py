"""Short live isolation check; run with E2B_API_KEY in the environment.

This creates one default sandbox for at most 60 seconds and always kills it.
No repository source or service secrets are copied into the sandbox.
"""

from __future__ import annotations

import json
import time

from e2b import Sandbox
from e2b.sandbox.commands.command_handle import CommandExitException


def main() -> None:
    sandbox = Sandbox.create(timeout=60, secure=True, allow_internet_access=False, envs={})
    started = time.monotonic()
    try:
        local = sandbox.commands.run("python -c 'print(6 * 7)'", timeout=10)
        try:
            network = sandbox.commands.run(
                "curl --noproxy '*' --connect-timeout 2 --max-time 4 -fsSI https://example.com",
                timeout=20,
            )
            internet_blocked = network.exit_code != 0
        except CommandExitException as exc:
            internet_blocked = exc.exit_code in {6, 7, 28}
        result = {
            "local_command_ok": local.exit_code == 0 and local.stdout.strip() == "42",
            "internet_blocked": internet_blocked,
            "duration_seconds": round(time.monotonic() - started, 2),
        }
        print(json.dumps(result))
        if not all((result["local_command_ok"], result["internet_blocked"])):
            raise SystemExit(1)
    finally:
        sandbox.kill()


if __name__ == "__main__":
    main()
