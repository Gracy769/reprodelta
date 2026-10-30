"""Additive, owner-scoped PostgreSQL storage for approved project profiles."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.domain.project_profile import ProfileApproval, ProjectProfileDraft


def _dsn(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def validation_matches_approval(profile: dict, approval: ProfileApproval, job: dict | None) -> bool:
    if job is None or job.get("status") != "completed" or job.get("job_kind") != "profile_validation":
        return False
    spec, result = job.get("spec_json") or {}, job.get("result_json") or {}
    return (
        str(job.get("id")) == str(approval.validation_job_id)
        and str(spec.get("profile_id")) == str(profile.get("id"))
        and spec.get("profile_digest") == approval.profile_digest == profile.get("profile_digest")
        and spec.get("contract_hash") == profile.get("contract_hash")
        and spec.get("provider") == profile.get("provider")
        and spec.get("repository") == profile.get("repository")
        and spec.get("sandbox_provider", "e2b") == profile.get("draft_json", {}).get(
            "sandbox_provider", "e2b"
        )
        and spec.get("old") == approval.old.model_dump(mode="json")
        and spec.get("new") == approval.new.model_dump(mode="json")
        and result.get("sample_validated") is True
        and result.get("repeatable") is True
        and isinstance(result.get("old_repeat"), dict)
        and isinstance(result.get("new_repeat"), dict)
        and (approval.validated_source_sha256 is None
             or approval.validated_source_sha256 == result.get("source_sha256"))
        and result.get("profile_digest") == spec.get("profile_digest")
        and result.get("contract_hash") == spec.get("contract_hash")
        and result.get("source_sha") == spec.get("source_sha")
        and isinstance(result.get("source_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", result["source_sha256"]) is not None
        and isinstance(result.get("input_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", result["input_sha256"]) is not None
    )


class ProjectProfileStore:
    def __init__(self, database_url: str):
        self.dsn = _dsn(database_url)

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)

    @staticmethod
    def _ensure(conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS project_profile_versions (
            id UUID PRIMARY KEY, owner_id TEXT NOT NULL, provider TEXT NOT NULL,
            repository TEXT NOT NULL, version INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'approved')),
            draft_json JSONB NOT NULL, profile_digest TEXT NOT NULL,
            contract_hash TEXT NOT NULL, approval_json JSONB,
            approved_by TEXT, created_at TIMESTAMPTZ NOT NULL,
            approved_at TIMESTAMPTZ,
            UNIQUE(owner_id, provider, repository, version)
        )""")
        conn.execute("""CREATE INDEX IF NOT EXISTS profile_versions_owner_idx
            ON project_profile_versions(owner_id, created_at DESC)""")
        conn.execute("""CREATE INDEX IF NOT EXISTS profile_versions_pending_idx
            ON project_profile_versions(created_at ASC) WHERE status='draft'""")

    @staticmethod
    def _row(row: dict | None) -> dict | None:
        if row is None:
            return None
        return {
            **row,
            "id": str(row["id"]),
            "created_at": row["created_at"].isoformat(),
            "approved_at": row["approved_at"].isoformat() if row["approved_at"] else None,
        }

    def create(self, owner_id: str, draft: ProjectProfileDraft) -> dict:
        with self._connect() as conn:
            self._ensure(conn)
            # Serialize numbering for this owner's repository without mutating
            # any older approved version.
            key = f"{owner_id}:{draft.provider}:{draft.repository}"
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
            row = conn.execute(
                """SELECT COALESCE(MAX(version), 0) + 1 AS version
                FROM project_profile_versions WHERE owner_id=%s AND provider=%s AND repository=%s""",
                (owner_id, draft.provider, draft.repository),
            ).fetchone()
            profile_id = uuid.uuid4()
            result = conn.execute(
                """INSERT INTO project_profile_versions
                (id, owner_id, provider, repository, version, status, draft_json,
                 profile_digest, contract_hash, created_at)
                VALUES (%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s) RETURNING *""",
                (
                    profile_id,
                    owner_id,
                    draft.provider,
                    draft.repository,
                    row["version"],
                    Jsonb(draft.model_dump(mode="json")),
                    draft.digest,
                    draft.contract_hash,
                    datetime.now(UTC),
                ),
            ).fetchone()
            return self._row(result)

    def list(self, owner_id: str) -> list[dict]:
        with self._connect() as conn:
            self._ensure(conn)
            rows = conn.execute(
                """SELECT * FROM project_profile_versions
                WHERE owner_id=%s ORDER BY created_at DESC LIMIT 100""",
                (owner_id,),
            ).fetchall()
            return [self._row(row) for row in rows]

    def list_pending(self) -> list[dict]:
        """Operator-only review queue; callers must check approver identity."""
        with self._connect() as conn:
            self._ensure(conn)
            rows = conn.execute(
                """SELECT * FROM project_profile_versions
                WHERE status='draft' ORDER BY created_at ASC LIMIT 100"""
            ).fetchall()
            return [self._row(row) for row in rows]

    def get(self, owner_id: str, profile_id: uuid.UUID) -> dict | None:
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                "SELECT * FROM project_profile_versions WHERE owner_id=%s AND id=%s",
                (owner_id, profile_id),
            ).fetchone()
            return self._row(row)

    def get_any(self, profile_id: uuid.UUID) -> dict | None:
        """Operator-only lookup; never expose this method through an owner route."""
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                "SELECT * FROM project_profile_versions WHERE id=%s", (profile_id,)
            ).fetchone()
            return self._row(row)

    def approve(
        self, owner_id: str, profile_id: uuid.UUID, approver: str, approval: ProfileApproval
    ) -> dict | None:
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                """SELECT * FROM project_profile_versions
                WHERE owner_id=%s AND id=%s FOR UPDATE""",
                (owner_id, profile_id),
            ).fetchone()
            if row is None:
                return None
            if row["status"] != "draft" or row["profile_digest"] != approval.profile_digest:
                raise ValueError("Profile was already approved or its digest changed")
            table = conn.execute(
                "SELECT to_regclass('project_run_jobs') AS table_name"
            ).fetchone()["table_name"]
            if table is None:
                raise ValueError("A completed sample validation is required before approval")
            validation = conn.execute(
                """SELECT * FROM project_run_jobs WHERE id=%s AND owner_id=%s
                AND job_kind='profile_validation' FOR UPDATE""",
                (approval.validation_job_id, owner_id),
            ).fetchone()
            if not validation_matches_approval(row, approval, validation):
                raise ValueError("A matching completed sample validation is required")
            bound_approval = approval.model_copy(update={
                "validated_source_sha256": validation["result_json"]["source_sha256"]
            })
            result = conn.execute(
                """UPDATE project_profile_versions SET status='approved',
                approval_json=%s, approved_by=%s, approved_at=%s
                WHERE id=%s RETURNING *""",
                (Jsonb(bound_approval.model_dump(mode="json")), approver, datetime.now(UTC), profile_id),
            ).fetchone()
            return self._row(result)
