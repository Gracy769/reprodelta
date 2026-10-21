"""Owner-scoped encrypted snapshots, addressed by their validated source digest."""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import zlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.rows import dict_row

from app.providers.e2b_runner import MAX_SNAPSHOT_BYTES, validate_snapshot
from app.storage.project_profiles import _dsn

RETENTION_DAYS = 7
MAX_ENCODED_BYTES = 15_000_000


class ProjectSnapshotStore:
    def __init__(self, database_url: str, key: str):
        try:
            decoded = base64.b64decode(key, altchars=b"-_", validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("PROJECT_SNAPSHOT_KEY must be a base64-encoded 32-byte key") from exc
        if len(decoded) != 32:
            raise ValueError("PROJECT_SNAPSHOT_KEY must be a base64-encoded 32-byte key")
        self.dsn = _dsn(database_url)
        self.aead = AESGCM(decoded)

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)

    @staticmethod
    def _ensure(conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS project_source_snapshots (
            owner_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
            nonce BYTEA NOT NULL, ciphertext BYTEA NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY(owner_id, source_sha256)
        )""")
        conn.execute("""CREATE INDEX IF NOT EXISTS project_snapshots_expiry_idx
            ON project_source_snapshots(expires_at)""")

    @staticmethod
    def _aad(owner_id: str, digest: str) -> bytes:
        if not owner_id or len(owner_id) > 255 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("A valid owner and source digest are required")
        return f"reprodelta-snapshot-v1\0{owner_id}\0{digest}".encode()

    @staticmethod
    def _encode(files: Mapping[str, bytes]) -> bytes:
        body = {
            name: base64.b64encode(content).decode("ascii")
            for name, content in sorted(files.items())
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > MAX_ENCODED_BYTES:
            raise ValueError("Encoded source snapshot exceeds the pilot limit")
        return zlib.compress(encoded, level=6)

    @staticmethod
    def _decode(payload: bytes) -> dict[str, bytes]:
        decompressor = zlib.decompressobj()
        try:
            encoded = decompressor.decompress(payload, MAX_ENCODED_BYTES + 1)
        except zlib.error as exc:
            raise ValueError("Stored source snapshot is invalid") from exc
        if len(encoded) > MAX_ENCODED_BYTES or not decompressor.eof or decompressor.unused_data:
            raise ValueError("Stored source snapshot exceeds its bound")
        body = json.loads(encoded)
        if not isinstance(body, dict):
            raise ValueError("Stored source snapshot is invalid")
        return {
            name: base64.b64decode(content, validate=True)
            for name, content in body.items()
        }

    def save(self, owner_id: str, files: Mapping[str, bytes]) -> str:
        digest = validate_snapshot(files)
        aad = self._aad(owner_id, digest)
        nonce = secrets.token_bytes(12)
        encrypted = self.aead.encrypt(nonce, self._encode(files), aad)
        now = datetime.now(UTC)
        with self._connect() as conn:
            self._ensure(conn)
            conn.execute("""DELETE FROM project_source_snapshots
                WHERE owner_id=%s AND source_sha256=%s AND expires_at<=now()""",
                (owner_id, digest),
            )
            conn.execute("""INSERT INTO project_source_snapshots
                (owner_id, source_sha256, nonce, ciphertext, created_at, expires_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(owner_id, source_sha256) DO NOTHING""",
                (owner_id, digest, nonce, encrypted, now, now + timedelta(days=RETENTION_DAYS)),
            )
        return digest

    def get(self, owner_id: str, digest: str) -> dict[str, bytes] | None:
        aad = self._aad(owner_id, digest)
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute("""SELECT nonce, ciphertext FROM project_source_snapshots
                WHERE owner_id=%s AND source_sha256=%s AND expires_at>now()""",
                (owner_id, digest),
            ).fetchone()
        if row is None:
            return None
        files = self._decode(self.aead.decrypt(bytes(row["nonce"]), bytes(row["ciphertext"]), aad))
        if validate_snapshot(files) != digest or sum(map(len, files.values())) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Stored source snapshot digest mismatch")
        return files

    def purge_expired(self) -> int:
        with self._connect() as conn:
            self._ensure(conn)
            result = conn.execute(
                "DELETE FROM project_source_snapshots WHERE expires_at<=now()"
            )
            return result.rowcount
