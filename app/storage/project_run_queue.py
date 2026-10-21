"""Postgres leased queue for approved-project jobs; no process-local verdict state."""

from __future__ import annotations

import hashlib
import json
import re
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.domain.profile_validation import ProfileValidationSpec
from app.domain.project_run import ProjectRunSpec
from app.storage.project_profiles import _dsn


class ProjectRunQueue:
    def __init__(self, database_url: str):
        self.dsn = _dsn(database_url)

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)

    @staticmethod
    def _ensure(conn) -> None:
        conn.execute("""CREATE TABLE IF NOT EXISTS project_run_jobs (
            id UUID PRIMARY KEY, owner_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, spec_json JSONB NOT NULL,
            job_kind TEXT NOT NULL DEFAULT 'investigation',
            spec_sha256 TEXT NOT NULL, status TEXT NOT NULL
                CHECK (status IN ('queued','running','completed','failed','cancelled')),
            lease_token UUID, lease_expires_at TIMESTAMPTZ,
            cancel_requested_at TIMESTAMPTZ, result_json JSONB,
            result_sha256 TEXT,
            error_code TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(owner_id, idempotency_key)
        )""")
        conn.execute("""ALTER TABLE project_run_jobs ADD COLUMN IF NOT EXISTS
            job_kind TEXT NOT NULL DEFAULT 'investigation'""")
        conn.execute("""ALTER TABLE project_run_jobs ADD COLUMN IF NOT EXISTS
            result_sha256 TEXT""")
        conn.execute("""CREATE INDEX IF NOT EXISTS project_run_jobs_queue_idx
            ON project_run_jobs(created_at, id) WHERE status='queued'""")
        conn.execute("""CREATE INDEX IF NOT EXISTS project_run_jobs_owner_idx
            ON project_run_jobs(owner_id, created_at DESC)""")

    @staticmethod
    def _row(row):
        if row is None:
            return None
        return {
            **row,
            "id": str(row["id"]),
            "lease_token": str(row["lease_token"]) if row["lease_token"] else None,
        }

    def _submit(
        self, owner_id: str, key: str,
        spec: ProjectRunSpec | ProfileValidationSpec, kind: str,
    ) -> dict:
        if not owner_id or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", key):
            raise ValueError("An owner and an 8–128 character idempotency key are required")
        if len(json.dumps(spec.model_dump(mode="json"))) > 16_384:
            raise ValueError("Job manifest is too large")
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                """INSERT INTO project_run_jobs
                (id, owner_id, idempotency_key, spec_json, spec_sha256, job_kind, status)
                VALUES (%s,%s,%s,%s,%s,%s,'queued')
                ON CONFLICT(owner_id, idempotency_key) DO UPDATE
                SET idempotency_key=EXCLUDED.idempotency_key RETURNING *""",
                (uuid.uuid4(), owner_id, key, Jsonb(spec.model_dump(mode="json")), spec.digest,
                 kind),
            ).fetchone()
            if row["spec_sha256"] != spec.digest or row["job_kind"] != kind:
                raise ValueError("Idempotency key was already used for a different run")
            return self._row(row)

    def submit(self, owner_id: str, key: str, spec: ProjectRunSpec) -> dict:
        return self._submit(owner_id, key, spec, "investigation")

    def submit_validation(self, owner_id: str, key: str, spec: ProfileValidationSpec) -> dict:
        return self._submit(owner_id, key, spec, "profile_validation")

    def get(
        self, owner_id: str, job_id: uuid.UUID, *, kind: str = "investigation"
    ) -> dict | None:
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                "SELECT * FROM project_run_jobs WHERE id=%s AND owner_id=%s AND job_kind=%s",
                (job_id, owner_id, kind),
            ).fetchone()
            return self._row(row)

    def list(self, owner_id: str) -> list[dict]:
        with self._connect() as conn:
            self._ensure(conn)
            rows = conn.execute(
                """SELECT * FROM project_run_jobs WHERE owner_id=%s AND job_kind='investigation'
                ORDER BY created_at DESC LIMIT 50""",
                (owner_id,),
            ).fetchall()
            return [self._row(row) for row in rows]

    def claim(self, allowed_kinds: tuple[str, ...] = ("investigation",)) -> dict | None:
        """Atomically claim one job, allowing one running per owner and two global."""
        if not allowed_kinds or any(
            kind not in {"investigation", "profile_validation"} for kind in allowed_kinds
        ):
            raise ValueError("At least one supported job kind is required")
        with self._connect() as conn:
            self._ensure(conn)
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('reprodelta-project-queue-v1'))")
            conn.execute("""UPDATE project_run_jobs SET status='failed', error_code='worker_lost',
                lease_token=NULL, lease_expires_at=NULL, finished_at=now(), updated_at=now()
                WHERE status='running' AND lease_expires_at <= now()""")
            active = conn.execute(
                "SELECT count(*) AS n FROM project_run_jobs WHERE status='running'"
            ).fetchone()["n"]
            if active >= 2:
                return None
            row = conn.execute("""SELECT q.id FROM project_run_jobs q
                WHERE q.status='queued' AND q.job_kind=ANY(%s) AND NOT EXISTS (
                    SELECT 1 FROM project_run_jobs r
                    WHERE r.owner_id=q.owner_id AND r.status='running')
                ORDER BY q.created_at, q.id FOR UPDATE OF q SKIP LOCKED LIMIT 1""",
                (list(allowed_kinds),),
            ).fetchone()
            if row is None:
                return None
            token = uuid.uuid4()
            claimed = conn.execute(
                """UPDATE project_run_jobs
                SET status='running', lease_token=%s,
                    lease_expires_at=now()+interval '90 seconds',
                    started_at=now(), updated_at=now()
                WHERE id=%s AND status='queued' RETURNING *""",
                (token, row["id"]),
            ).fetchone()
            return self._row(claimed)

    def heartbeat(self, job_id: uuid.UUID, lease_token: uuid.UUID) -> bool:
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                """UPDATE project_run_jobs
                SET lease_expires_at=now()+interval '90 seconds', updated_at=now()
                WHERE id=%s AND lease_token=%s AND status='running'
                    AND lease_expires_at>now() AND cancel_requested_at IS NULL
                RETURNING id""",
                (job_id, lease_token),
            ).fetchone()
            return row is not None

    def cancellation_requested(self, job_id: uuid.UUID, lease_token: uuid.UUID) -> bool:
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                """SELECT cancel_requested_at IS NOT NULL AS requested
                FROM project_run_jobs WHERE id=%s AND lease_token=%s AND status='running'""",
                (job_id, lease_token),
            ).fetchone()
            return row is None or row["requested"]

    def cancel(
        self, owner_id: str, job_id: uuid.UUID, *, kind: str = "investigation"
    ) -> dict | None:
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                """UPDATE project_run_jobs SET
                status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,
                cancel_requested_at=CASE WHEN status='running' THEN now()
                    ELSE cancel_requested_at END,
                finished_at=CASE WHEN status='queued' THEN now() ELSE finished_at END,
                updated_at=now()
                WHERE id=%s AND owner_id=%s AND job_kind=%s
                    AND status IN ('queued','running')
                RETURNING *""",
                (job_id, owner_id, kind),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM project_run_jobs WHERE id=%s AND owner_id=%s AND job_kind=%s",
                    (job_id, owner_id, kind),
                ).fetchone()
            return self._row(row)

    def finish(
        self,
        job_id: uuid.UUID,
        lease_token: uuid.UUID,
        *,
        status: str,
        result: dict | None = None,
        error_code: str | None = None,
    ) -> bool:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Invalid terminal status")
        if status == "completed" and (result is None or error_code is not None):
            raise ValueError("Completed jobs require a result without an error")
        if status != "completed" and result is not None:
            raise ValueError("Failed or cancelled jobs cannot carry a finding")
        if status == "failed" and error_code is None:
            raise ValueError("Failed jobs require a stable error code")
        if error_code is not None and not re.fullmatch(r"[a-z_]{1,64}", error_code):
            raise ValueError("Error code must be a bounded identifier")
        result_digest = None
        if result is not None:
            encoded = json.dumps(
                result, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            if len(encoded) > 262_144:
                raise ValueError("Job result exceeds 256 KB")
            result_digest = hashlib.sha256(encoded).hexdigest()
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                """UPDATE project_run_jobs SET status=%s,
                result_json=%s, result_sha256=%s, error_code=%s, lease_token=NULL,
                lease_expires_at=NULL, finished_at=now(), updated_at=now()
                WHERE id=%s AND lease_token=%s AND status='running'
                    AND lease_expires_at>now()
                    AND (%s <> 'completed' OR cancel_requested_at IS NULL)
                RETURNING id""",
                (
                    status,
                    Jsonb(result) if result is not None else None,
                    result_digest,
                    error_code,
                    job_id,
                    lease_token,
                    status,
                ),
            ).fetchone()
            return row is not None
