"""Revocable owner-issued public copies of approved-project findings."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.storage.project_profiles import _dsn
from app.storage.project_run_queue import ProjectRunQueue


class ProjectShareStore:
    def __init__(self, database_url: str):
        self.dsn = _dsn(database_url)

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)

    @staticmethod
    def _ensure(conn):
        ProjectRunQueue._ensure(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS project_run_shares (
            id UUID PRIMARY KEY, owner_id TEXT NOT NULL,
            job_id UUID NOT NULL REFERENCES project_run_jobs(id),
            token_sha256 TEXT NOT NULL UNIQUE, copy_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at TIMESTAMPTZ
        )""")
        conn.execute("""CREATE INDEX IF NOT EXISTS project_run_shares_owner_idx
            ON project_run_shares(owner_id, job_id, created_at DESC)""")

    def create(self, owner_id: str, job_id: uuid.UUID, copy: dict) -> dict:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                """INSERT INTO project_run_shares
                (id, owner_id, job_id, token_sha256, copy_json)
                SELECT %s, %s, id, %s, %s FROM project_run_jobs
                WHERE id=%s AND owner_id=%s AND job_kind='investigation'
                AND status='completed' RETURNING id, created_at""",
                (uuid.uuid4(), owner_id, digest, Jsonb(copy), job_id, owner_id),
            ).fetchone()
            if row is None:
                raise ValueError("Completed project run not found")
            return {"id": str(row["id"]), "token": token, "created_at": row["created_at"].isoformat()}

    def list(self, owner_id: str, job_id: uuid.UUID) -> list[dict]:
        with self._connect() as conn:
            self._ensure(conn)
            rows = conn.execute(
                """SELECT id, created_at, revoked_at FROM project_run_shares
                WHERE owner_id=%s AND job_id=%s ORDER BY created_at DESC LIMIT 50""",
                (owner_id, job_id),
            ).fetchall()
            return [{"id": str(row["id"]), "created_at": row["created_at"].isoformat(),
                     "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None}
                    for row in rows]

    def public(self, token: str) -> dict | None:
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            return None
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                """SELECT copy_json FROM project_run_shares
                WHERE token_sha256=%s AND revoked_at IS NULL""", (digest,)
            ).fetchone()
            return row["copy_json"] if row else None

    def revoke(self, owner_id: str, job_id: uuid.UUID, share_id: uuid.UUID) -> bool:
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                """UPDATE project_run_shares SET revoked_at=COALESCE(revoked_at, now())
                WHERE id=%s AND job_id=%s AND owner_id=%s RETURNING id""",
                (share_id, job_id, owner_id),
            ).fetchone()
            return row is not None
