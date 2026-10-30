"""Representative v2 inventory digests for tests; production uses built image hashes."""

import hashlib


def fixture_lock_digest(version: str) -> str:
    return hashlib.sha256(
        f"reprodelta-lock-v2\nPython 3.12.13\nnumpy=={version}\npip==25.0\n".encode()
    ).hexdigest()
