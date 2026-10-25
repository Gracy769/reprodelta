"""One owner-requested model proposal per completed project investigation."""

from __future__ import annotations

import math
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.domain.project_hypothesis import summary_digest
from app.storage.project_profiles import _dsn


class ProjectHypothesisStore:
    def __init__(self, database_url: str):
        self.dsn = _dsn(database_url)

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)

    @staticmethod
    def _ensure(conn) -> None:
        conn.execute("""CREATE TABLE IF NOT EXISTS project_model_hypotheses (
            id UUID PRIMARY KEY, owner_id TEXT NOT NULL,
            run_id UUID NOT NULL REFERENCES project_run_jobs(id),
            status TEXT NOT NULL CHECK (status IN ('pending','completed','failed')),
            summary_json JSONB NOT NULL, summary_sha256 TEXT NOT NULL,
            model_id TEXT NOT NULL, prompt_version TEXT NOT NULL,
            request_digest TEXT, response_text TEXT, usage_json JSONB,
            reserved_model_cost_usd DOUBLE PRECISION NOT NULL,
            cost_uncertain BOOLEAN NOT NULL DEFAULT TRUE,
            error_code TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ, UNIQUE(owner_id, run_id)
        )""")

    @staticmethod
    def _row(row: dict | None) -> dict | None:
        if row is None:
            return None
        return {key: value for key, value in row.items() if key != "owner_id"}

    def get(self, owner_id: str, run_id: uuid.UUID) -> dict | None:
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute(
                "SELECT * FROM project_model_hypotheses WHERE owner_id=%s AND run_id=%s",
                (owner_id, run_id),
            ).fetchone()
            return self._row(row)

    def reserve(self, owner_id: str, run_id: uuid.UUID, *, summary: dict,
                model_id: str, ceiling_usd: float) -> tuple[dict, bool]:
        if not math.isfinite(ceiling_usd) or not 0 < ceiling_usd <= 100:
            raise ValueError("A bounded model cost estimate is required")
        if not model_id or len(model_id) > 160:
            raise ValueError("Invalid model ID")
        proposal_id = uuid.uuid4()
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute("""INSERT INTO project_model_hypotheses
                (id, owner_id, run_id, status, summary_json, summary_sha256,
                 model_id, prompt_version, reserved_model_cost_usd)
                VALUES (%s,%s,%s,'pending',%s,%s,%s,'project_hypothesis_v1',%s)
                ON CONFLICT (owner_id, run_id) DO NOTHING RETURNING *""",
                (proposal_id, owner_id, run_id, Jsonb(summary), summary_digest(summary),
                 model_id, ceiling_usd),
            ).fetchone()
            if row is not None:
                return self._row(row), True
            existing = conn.execute(
                "SELECT * FROM project_model_hypotheses WHERE owner_id=%s AND run_id=%s",
                (owner_id, run_id),
            ).fetchone()
            return self._row(existing), False

    def complete(self, owner_id: str, run_id: uuid.UUID, *, request_digest: str,
                 response_text: str, usage: dict | None) -> dict | None:
        if len(response_text.encode("utf-8")) > 16_384:
            raise ValueError("Model proposal exceeded its response bound")
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute("""UPDATE project_model_hypotheses SET
                status='completed', request_digest=%s, response_text=%s,
                usage_json=%s, finished_at=now()
                WHERE owner_id=%s AND run_id=%s AND status='pending' RETURNING *""",
                (request_digest, response_text, Jsonb(usage or {}), owner_id, run_id),
            ).fetchone()
            return self._row(row)

    def fail(self, owner_id: str, run_id: uuid.UUID) -> dict | None:
        with self._connect() as conn:
            self._ensure(conn)
            row = conn.execute("""UPDATE project_model_hypotheses SET
                status='failed', error_code='model_unavailable', finished_at=now()
                WHERE owner_id=%s AND run_id=%s AND status='pending' RETURNING *""",
                (owner_id, run_id),
            ).fetchone()
            return self._row(row)
