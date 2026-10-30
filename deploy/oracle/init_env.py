"""Create the VM-only Compose environment file with strong local secrets."""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE / ".env.example"
TARGET = HERE / ".env"


def main() -> None:
    if TARGET.exists():
        raise SystemExit(f"{TARGET} already exists; refusing to overwrite it")
    db_password = secrets.token_hex(32)
    snapshot_key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    config = EXAMPLE.read_text(encoding="utf-8")
    config = config.replace("replace-with-random-hex", db_password)
    config = config.replace(
        "replace-with-a-random-secret-of-at-least-32-characters", secrets.token_hex(32)
    )
    config = config.replace("PROJECT_SNAPSHOT_KEY=\n", f"PROJECT_SNAPSHOT_KEY={snapshot_key}\n")
    descriptor = os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(config)
    print(f"Created {TARGET} with mode 0600. Set PUBLIC_DOMAIN and provider variables before deploy.")


if __name__ == "__main__":
    main()
