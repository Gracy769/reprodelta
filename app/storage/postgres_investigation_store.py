"""Durable store for the hosted curated demo.

This intentionally uses a small deployment-specific schema. The broader
research schema in ``models.py`` is not yet wired into the investigation API.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.storage.investigation_store import InvestigationStore, _check_transition


class PostgresInvestigationStore(InvestigationStore):
    """Persist investigations, stage events, and approvals across deployments."""

    def __init__(self, database_url: str) -> None:
        self._dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        self._known_running: set[str] = set()
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS demo_investigations (
                    id UUID PRIMARY KEY,
                    project_id UUID NOT NULL,
                    budget_json JSONB NOT NULL,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    report_json JSONB,
                    report_text TEXT,
                    evidence JSONB
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS demo_events (
                    investigation_id UUID NOT NULL REFERENCES demo_investigations(id),
                    sequence INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (investigation_id, sequence)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS demo_approvals (
                    project_id UUID PRIMARY KEY,
                    contract_hash TEXT NOT NULL,
                    approved_at TIMESTAMPTZ NOT NULL,
                    approved_by TEXT NOT NULL
                )
            """)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=10)

    @staticmethod
    def _uuid(value: str) -> uuid.UUID:
        return uuid.UUID(value)

    @staticmethod
    def _event(
        conn: psycopg.Connection,
        investigation_id: uuid.UUID,
        actor: str,
        event_type: str,
        payload: dict | None = None,
    ) -> None:
        # The parent row is locked by each caller before allocating a sequence.
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), -1) + 1 AS next_seq "
            "FROM demo_events WHERE investigation_id = %s",
            (investigation_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO demo_events VALUES (%s, %s, %s, %s, %s, %s)",
            (
                investigation_id,
                row["next_seq"],
                actor,
                event_type,
                Jsonb(payload or {}),
                datetime.now(UTC),
            ),
        )

    def admission_records(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id FROM demo_investigations WHERE created_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' OR status='running'").fetchall()
        return [record for row in rows if (record := self.get_investigation(str(row["id"]))) is not None]

    def create_investigation(
        self, project_id: str, budget_json: dict, owner_id: str = "user"
    ) -> str:
        investigation_id = uuid.uuid4()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO demo_investigations "
                "(id, project_id, budget_json, owner_id, status, created_at) "
                "VALUES (%s, %s, %s, %s, 'draft', %s)",
                (
                    investigation_id,
                    self._uuid(project_id),
                    Jsonb(budget_json),
                    owner_id,
                    datetime.now(UTC),
                ),
            )
        return str(investigation_id)

    def check_ready(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1").fetchone()

    def reserve_budget(self, investigation_id: str, **amounts) -> dict:
        from app.orchestrator.budget import reserve
        inv_id = self._uuid(investigation_id)
        with self._connect() as conn:
            row = conn.execute("SELECT budget_json FROM demo_investigations WHERE id = %s FOR UPDATE", (inv_id,)).fetchone()
            if row is None:
                raise KeyError(investigation_id)
            budget = row["budget_json"]
            usage = reserve(budget.get("usage", {}), budget, **amounts)
            conn.execute("UPDATE demo_investigations SET budget_json = %s WHERE id = %s", (Jsonb({**budget, "usage": usage}), inv_id))
            self._event(conn, inv_id, "budget", "budget_reserved", usage)
            return usage

    def transition_status(self, investigation_id: str, new_status: str) -> None:
        inv_id = self._uuid(investigation_id)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM demo_investigations WHERE id = %s FOR UPDATE",
                (inv_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Investigation {investigation_id!r} not found")
            _check_transition(row["status"], new_status)
            conn.execute(
                "UPDATE demo_investigations SET status = %s WHERE id = %s",
                (new_status, inv_id),
            )
            if new_status == "running":
                self._known_running.add(investigation_id)
            else:
                self._known_running.discard(investigation_id)

    def append_event(
        self, investigation_id: str, actor: str, event_type: str, payload: dict | None = None
    ) -> None:
        inv_id = self._uuid(investigation_id)
        with self._connect() as conn:
            if (
                conn.execute(
                    "SELECT id FROM demo_investigations WHERE id = %s FOR UPDATE",
                    (inv_id,),
                ).fetchone()
                is None
            ):
                raise KeyError(investigation_id)
            self._event(conn, inv_id, actor, event_type, payload)

    def store_report(self, investigation_id: str, report_json: dict, report_text: str) -> None:
        inv_id = self._uuid(investigation_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT report_json, report_text FROM demo_investigations WHERE id = %s FOR UPDATE",
                (inv_id,),
            ).fetchone()
            if row is None:
                raise KeyError(investigation_id)
            if row["report_json"] is not None:
                if row["report_json"] != report_json or row["report_text"] != report_text:
                    raise ValueError("Final report is immutable")
                return
            conn.execute(
                "UPDATE demo_investigations SET report_json = %s, report_text = %s WHERE id = %s",
                (Jsonb(report_json), report_text, inv_id),
            )
            self._event(conn, inv_id, "runner", "report_assembled")

    def get_investigation(self, investigation_id: str) -> dict | None:
        inv_id = self._uuid(investigation_id)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, project_id, owner_id, status, budget_json, report_json, created_at "
                "FROM demo_investigations WHERE id = %s FOR UPDATE",
                (inv_id,),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == "running" and investigation_id not in self._known_running:
                conn.execute(
                    "UPDATE demo_investigations SET status = 'interrupted' WHERE id = %s",
                    (inv_id,),
                )
                self._event(
                    conn,
                    inv_id,
                    "system",
                    "investigation_interrupted",
                    {"reason": "process_restart"},
                )
                row["status"] = "interrupted"
            return {
                "id": str(row["id"]),
                "project_id": str(row["project_id"]),
                "owner_id": row["owner_id"],
                "status": row["status"],
                "budget_json": row["budget_json"],
                "report_available": row["status"] == "completed" and row["report_json"] is not None,
                "created_at": row["created_at"].isoformat(),
            }

    def get_report(self, investigation_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT report_json FROM demo_investigations WHERE id = %s",
                (self._uuid(investigation_id),),
            ).fetchone()
        return row["report_json"] if row else None

    def get_report_text(self, investigation_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT report_text FROM demo_investigations WHERE id = %s",
                (self._uuid(investigation_id),),
            ).fetchone()
        return row["report_text"] if row else None

    def store_evidence(self, investigation_id: str, evidence: dict) -> None:
        inv_id = self._uuid(investigation_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT evidence FROM demo_investigations WHERE id = %s FOR UPDATE",
                (inv_id,),
            ).fetchone()
            if row is None:
                raise KeyError(investigation_id)
            if row["evidence"] is not None and row["evidence"] != evidence:
                raise ValueError("Final evidence is immutable")
            conn.execute(
                "UPDATE demo_investigations SET evidence = %s WHERE id = %s",
                (Jsonb(evidence), inv_id),
            )

    def get_evidence(self, investigation_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT evidence FROM demo_investigations WHERE id = %s",
                (self._uuid(investigation_id),),
            ).fetchone()
        return row["evidence"] if row else None

    def approve_contract(self, project_id: str, contract_hash: str) -> dict:
        proj_id = self._uuid(project_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO demo_approvals VALUES (%s, %s, %s, 'user') "
                "ON CONFLICT (project_id) DO NOTHING",
                (proj_id, contract_hash, datetime.now(UTC)),
            )
            row = conn.execute(
                "SELECT contract_hash, approved_at, approved_by "
                "FROM demo_approvals WHERE project_id = %s",
                (proj_id,),
            ).fetchone()
            if row["contract_hash"] != contract_hash:
                raise ValueError("Approved contract hash is immutable")
        return {
            "approved": True,
            "contract_hash": row["contract_hash"],
            "approved_at": row["approved_at"].isoformat(),
            "approved_by": row["approved_by"],
        }

    def get_approval(self, project_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT contract_hash, approved_at, approved_by "
                "FROM demo_approvals WHERE project_id = %s",
                (self._uuid(project_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "approved": True,
            "contract_hash": row["contract_hash"],
            "approved_at": row["approved_at"].isoformat(),
            "approved_by": row["approved_by"],
        }

    def get_events(self, investigation_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT sequence, actor, event_type, payload, occurred_at "
                "FROM demo_events WHERE investigation_id = %s ORDER BY sequence",
                (self._uuid(investigation_id),),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "actor": row["actor"],
                "event_type": row["event_type"],
                "payload": row["payload"],
                "occurred_at": row["occurred_at"].isoformat(),
            }
            for row in rows
        ]
