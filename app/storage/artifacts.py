"""Content-addressed artifact storage.

Files are stored at:  {ARTIFACT_DIR}/{sha256[:2]}/{sha256}
The hash is computed over the raw bytes after writing to a temp file,
then the file is atomically moved into its final location.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from pathlib import Path

from app.config import get_settings
from app.domain.types import ArtifactRef


def _artifact_root() -> Path:
    return Path(get_settings().artifact_dir)


def store_artifact(
    data: bytes,
    media_type: str = "application/octet-stream",
) -> ArtifactRef:
    """Write *data* into the content-addressed store and return its ArtifactRef.

    Idempotent: if the content already exists the existing file is kept and a
    fresh ArtifactRef pointing to it is returned.
    """
    sha256 = hashlib.sha256(data).hexdigest()
    root = _artifact_root()
    bucket = root / sha256[:2]
    bucket.mkdir(parents=True, exist_ok=True)
    dest = bucket / sha256

    if not dest.exists():
        # Write to a temp file in the same directory for atomic rename.
        fd, tmp_path = tempfile.mkstemp(dir=bucket)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp_path, dest)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    return ArtifactRef.from_hash(sha256=sha256, byte_size=len(data), media_type=media_type)


def load_artifact(sha256: str) -> bytes:
    """Load artifact bytes by SHA-256 hex digest.

    Raises FileNotFoundError when the artifact is not present in the store.
    Raises ValueError when the stored bytes do not match the expected hash
    (i.e. the content was corrupted).
    """
    if len(sha256) != 64 or not all(c in "0123456789abcdef" for c in sha256):
        raise ValueError(f"Invalid sha256 hex digest: {sha256!r}")

    dest = _artifact_root() / sha256[:2] / sha256
    if not dest.exists():
        raise FileNotFoundError(f"Artifact not found: {sha256}")

    data = dest.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise ValueError(
            f"Artifact integrity failure: stored bytes hash to {actual!r}, expected {sha256!r}"
        )
    return data
