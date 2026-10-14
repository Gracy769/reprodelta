"""
Investigation state persistence.

Uses filesystem JSON under ARTIFACT_DIR for durability (FileSystemInvestigationStore)
or an in-memory dict (MemoryInvestigationStore) for tests.

An investigation that was "running" when the process crashed is marked
"interrupted" on next read — it is never silently presented as completed.

State machine (enforced on write):
  draft → running
  running → completed | failed | interrupted
  draft/running → cancelled
  Reject: completed/failed/interrupted → running

Events are append-only with monotonic sequence numbers.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"running", "cancelled"},
    "running": {"completed", "failed", "interrupted", "cancelled"},
    "completed": set(),
    "failed": set(),
    "interrupted": set(),
    "cancelled": set(),
}


def _check_transition(current: str, new: str) -> None:
    allowed = _VALID_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise ValueError(
            f"Invalid status transition: {current!r} → {new!r}. "
            f"Allowed from {current!r}: {sorted(allowed)}"
        )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class InvestigationStore(ABC):
    """Abstract store for investigation state."""

    @abstractmethod
    def create_investigation(
        self,
        project_id: str,
        budget_json: dict,
        owner_id: str = "user",
    ) -> str:
        """Create a draft investigation and return its ID (str UUID)."""

    @abstractmethod
    def transition_status(self, investigation_id: str, new_status: str) -> None:
        """Transition investigation status; raises ValueError on bad transition."""

    @abstractmethod
    def append_event(
        self,
        investigation_id: str,
        actor: str,
        event_type: str,
        payload: dict | None = None,
    ) -> None:
        """Append an event record (sequence number is auto-assigned)."""

    @abstractmethod
    def store_report(
        self,
        investigation_id: str,
        report_json: dict,
        report_text: str,
    ) -> None:
        """Persist the final report; idempotent on re-call."""

    @abstractmethod
    def get_investigation(self, investigation_id: str) -> dict | None:
        """Return status + budget + report_available or None if not found."""

    @abstractmethod
    def get_report(self, investigation_id: str) -> dict | None:
        """Return the stored report JSON or None."""

    @abstractmethod
    def get_report_text(self, investigation_id: str) -> str | None:
        """Return the stored text report or None."""

    @abstractmethod
    def store_evidence(self, investigation_id: str, evidence: dict) -> None:
        """Persist the complete typed evidence payload."""

    @abstractmethod
    def get_evidence(self, investigation_id: str) -> dict | None:
        """Return the stored evidence payload or None."""

    @abstractmethod
    def approve_contract(self, project_id: str, contract_hash: str) -> dict:
        """Record approval of the exact current contract hash."""

    @abstractmethod
    def get_approval(self, project_id: str) -> dict | None:
        """Return the project's approved contract hash, if any."""

    def check_ready(self) -> None:
        """Memory is ready; filesystem persistence checks its writable volume."""
        if hasattr(self, "_base"):
            self._base.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self._base) as probe:
                probe.write(b"ready")

    def reserve_budget(self, investigation_id: str, **amounts) -> dict:
        raise NotImplementedError("Budget reservations are unavailable in this store")

    def admission_records(self) -> list[dict]:
        """Internal quota metadata; never exposed as a cross-owner API listing."""
        raise NotImplementedError("Admission metadata is unavailable in this store")

    @abstractmethod
    def get_events(self, investigation_id: str) -> list[dict]:
        """Return all events in sequence order."""


# ---------------------------------------------------------------------------
# Memory implementation (tests / local dev without PERSIST_INVESTIGATIONS)
# ---------------------------------------------------------------------------


class MemoryInvestigationStore(InvestigationStore):
    """Thread-safe in-memory store.  State is lost on process restart."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # { id: {"id", "project_id", "budget_json", "status", "created_at", "report_json", "report_text"} }
        self._records: dict[str, dict[str, Any]] = {}
        # { id: [event, ...] }
        self._events: dict[str, list[dict]] = {}
        self._approvals: dict[str, dict] = {}

    def admission_records(self) -> list[dict]:
        with self._lock:
            return [{key: record[key] for key in ("id", "owner_id", "status", "created_at")}
                    for record in self._records.values()]

    def create_investigation(
        self,
        project_id: str,
        budget_json: dict,
        owner_id: str = "user",
    ) -> str:
        inv_id = str(uuid.uuid4())
        with self._lock:
            self._records[inv_id] = {
                "id": inv_id,
                "project_id": project_id,
                "budget_json": budget_json,
                "owner_id": owner_id,
                "status": "draft",
                "created_at": datetime.now(UTC).isoformat(),
                "report_json": None,
                "report_text": None,
                "evidence": None,
            }
            self._events[inv_id] = []
        return inv_id

    def reserve_budget(self, investigation_id: str, **amounts) -> dict:
        from app.orchestrator.budget import reserve
        with self._lock:
            record = self._records[investigation_id]
            budget = record["budget_json"]
            usage = reserve(budget.get("usage", {}), budget, **amounts)
            record["budget_json"] = {**budget, "usage": usage}
            return usage

    def transition_status(self, investigation_id: str, new_status: str) -> None:
        with self._lock:
            record = self._records.get(investigation_id)
            if record is None:
                raise KeyError(f"Investigation {investigation_id!r} not found")
            _check_transition(record["status"], new_status)
            record["status"] = new_status

    def append_event(
        self,
        investigation_id: str,
        actor: str,
        event_type: str,
        payload: dict | None = None,
    ) -> None:
        with self._lock:
            events = self._events.setdefault(investigation_id, [])
            seq = len(events)
            events.append(
                {
                    "sequence": seq,
                    "actor": actor,
                    "event_type": event_type,
                    "payload": payload or {},
                    "occurred_at": datetime.now(UTC).isoformat(),
                }
            )

    def store_report(
        self,
        investigation_id: str,
        report_json: dict,
        report_text: str,
    ) -> None:
        with self._lock:
            record = self._records.get(investigation_id)
            if record is None:
                raise KeyError(investigation_id)
            if record["report_json"] is not None:
                if record["report_json"] != report_json or record["report_text"] != report_text:
                    raise ValueError("Final report is immutable")
                return
            record["report_json"] = report_json
            record["report_text"] = report_text
        self.append_event(investigation_id, "runner", "report_assembled", {})

    def get_investigation(self, investigation_id: str) -> dict | None:
        with self._lock:
            record = self._records.get(investigation_id)
            if record is None:
                return None
            return {
                "id": record["id"],
                "project_id": record["project_id"],
                "owner_id": record["owner_id"],
                "status": record["status"],
                "budget_json": record["budget_json"],
                "report_available": record["report_json"] is not None,
                "created_at": record["created_at"],
            }

    def get_report(self, investigation_id: str) -> dict | None:
        with self._lock:
            record = self._records.get(investigation_id)
            if record is None:
                return None
            return record.get("report_json")

    def get_report_text(self, investigation_id: str) -> str | None:
        with self._lock:
            record = self._records.get(investigation_id)
            return record.get("report_text") if record else None

    def store_evidence(self, investigation_id: str, evidence: dict) -> None:
        with self._lock:
            record = self._records[investigation_id]
            if record["evidence"] is not None and record["evidence"] != evidence:
                raise ValueError("Final evidence is immutable")
            record["evidence"] = evidence

    def get_evidence(self, investigation_id: str) -> dict | None:
        with self._lock:
            record = self._records.get(investigation_id)
            return record.get("evidence") if record else None

    def approve_contract(self, project_id: str, contract_hash: str) -> dict:
        approval = {
            "approved": True,
            "contract_hash": contract_hash,
            "approved_at": datetime.now(UTC).isoformat(),
            "approved_by": "user",
        }
        with self._lock:
            existing = self._approvals.get(project_id)
            if existing is not None:
                if existing["contract_hash"] != contract_hash:
                    raise ValueError("Approved contract hash is immutable")
                return existing
            self._approvals[project_id] = approval
        return approval

    def get_approval(self, project_id: str) -> dict | None:
        with self._lock:
            return self._approvals.get(project_id)

    def get_events(self, investigation_id: str) -> list[dict]:
        with self._lock:
            return list(self._events.get(investigation_id, []))


# ---------------------------------------------------------------------------
# Filesystem implementation (prod / durable across restarts)
# ---------------------------------------------------------------------------


class FileSystemInvestigationStore(InvestigationStore):
    """
    Stores investigation state as JSON files under ARTIFACT_DIR/investigations/{id}/.

    Layout:
      state.json     — status, contract hash, budget, metadata
      events.jsonl   — append-only newline-delimited event log
      report.json    — final evidence report JSON
      report.txt     — text summary

    On startup, any investigation with status "running" is marked "interrupted"
    (process crash mid-run).

    Atomic writes: write to .tmp then os.replace().
    Contract hash is immutable once set in state.json.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        self._lock = threading.Lock()
        # Track IDs that this process instance created/started — these are
        # NOT crash survivors, so we don't interrupt them on read.
        self._known_running: set[str] = set()

    def _inv_dir(self, investigation_id: str) -> Path:
        return self._base / "investigations" / str(uuid.UUID(investigation_id))

    def _state_path(self, investigation_id: str) -> Path:
        return self._inv_dir(investigation_id) / "state.json"

    def _events_path(self, investigation_id: str) -> Path:
        return self._inv_dir(investigation_id) / "events.jsonl"

    def _report_path(self, investigation_id: str) -> Path:
        return self._inv_dir(investigation_id) / "report.json"

    def _report_txt_path(self, investigation_id: str) -> Path:
        return self._inv_dir(investigation_id) / "report.txt"

    def _evidence_path(self, investigation_id: str) -> Path:
        return self._inv_dir(investigation_id) / "evidence.json"

    def _approval_path(self, project_id: str) -> Path:
        # Project IDs come from the fixed demo UUID, but validate here too.
        return self._base / "approvals" / f"{uuid.UUID(project_id)}.json"

    def _write_atomic(self, path: Path, data: str) -> None:
        """Write string data atomically using a temp file + os.replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            for attempt in range(10):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 9 or os.name != "nt":
                        raise
                    time.sleep(0.02 * (attempt + 1))
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _read_state(self, investigation_id: str) -> dict | None:
        p = self._state_path(investigation_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def _write_state(self, investigation_id: str, state: dict) -> None:
        self._write_atomic(
            self._state_path(investigation_id),
            json.dumps(state, indent=2),
        )

    def _mark_interrupted_if_running(self, investigation_id: str) -> None:
        """If state shows "running" and this process didn't start it → interrupted."""
        if investigation_id in self._known_running:
            # This process started it — not a crash survivor
            return
        state = self._read_state(investigation_id)
        if state is not None and state.get("status") == "running":
            state["status"] = "interrupted"
            self._write_state(investigation_id, state)
            self._append_event_raw(
                investigation_id,
                {
                    "sequence": self._next_seq(investigation_id),
                    "actor": "system",
                    "event_type": "investigation_interrupted",
                    "payload": {"reason": "process_restart"},
                    "occurred_at": datetime.now(UTC).isoformat(),
                },
            )

    def _next_seq(self, investigation_id: str) -> int:
        p = self._events_path(investigation_id)
        if not p.exists():
            return 0
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return len(lines)

    def _append_event_raw(self, investigation_id: str, event: dict) -> None:
        p = self._events_path(investigation_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:  # noqa: PTH123
            fh.write(json.dumps(event) + "\n")

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------

    def admission_records(self) -> list[dict]:
        root = self._base / "investigations"
        records = []
        for path in root.glob("*/state.json"):
            record = self.get_investigation(path.parent.name)
            if record:
                records.append(record)
        return records

    def create_investigation(
        self,
        project_id: str,
        budget_json: dict,
        owner_id: str = "user",
    ) -> str:
        inv_id = str(uuid.uuid4())
        state = {
            "id": inv_id,
            "project_id": project_id,
            "budget_json": budget_json,
            "owner_id": owner_id,
            "status": "draft",
            "created_at": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            self._write_state(inv_id, state)
        return inv_id

    def reserve_budget(self, investigation_id: str, **amounts) -> dict:
        from app.orchestrator.budget import reserve
        with self._lock:
            state = self._read_state(investigation_id)
            if state is None:
                raise KeyError(investigation_id)
            budget = state["budget_json"]
            usage = reserve(budget.get("usage", {}), budget, **amounts)
            state["budget_json"] = {**budget, "usage": usage}
            self._write_state(investigation_id, state)
            return usage

    def transition_status(self, investigation_id: str, new_status: str) -> None:
        with self._lock:
            state = self._read_state(investigation_id)
            if state is None:
                raise KeyError(f"Investigation {investigation_id!r} not found")
            _check_transition(state["status"], new_status)
            state["status"] = new_status
            self._write_state(investigation_id, state)
            if new_status == "running":
                self._known_running.add(investigation_id)
            else:
                self._known_running.discard(investigation_id)

    def append_event(
        self,
        investigation_id: str,
        actor: str,
        event_type: str,
        payload: dict | None = None,
    ) -> None:
        with self._lock:
            seq = self._next_seq(investigation_id)
            event = {
                "sequence": seq,
                "actor": actor,
                "event_type": event_type,
                "payload": payload or {},
                "occurred_at": datetime.now(UTC).isoformat(),
            }
            self._append_event_raw(investigation_id, event)

    def store_report(
        self,
        investigation_id: str,
        report_json: dict,
        report_text: str,
    ) -> None:
        with self._lock:
            existing_json = self._report_path(investigation_id)
            existing_text = self._report_txt_path(investigation_id)
            if existing_json.exists() or existing_text.exists():
                if not existing_json.exists() or not existing_text.exists():
                    raise RuntimeError("Incomplete stored report")
                if (
                    json.loads(existing_json.read_text(encoding="utf-8")) != report_json
                    or existing_text.read_text(encoding="utf-8") != report_text
                ):
                    raise ValueError("Final report is immutable")
                return
            self._write_atomic(
                self._report_path(investigation_id),
                json.dumps(report_json, indent=2),
            )
            self._write_atomic(
                self._report_txt_path(investigation_id),
                report_text,
            )
        self.append_event(investigation_id, "runner", "report_assembled", {})

    def get_investigation(self, investigation_id: str) -> dict | None:
        with self._lock:
            self._mark_interrupted_if_running(investigation_id)
            state = self._read_state(investigation_id)
        if state is None:
            return None
        return {
            "id": state["id"],
            "project_id": state["project_id"],
            "owner_id": state.get("owner_id"),
            "status": state["status"],
            "budget_json": state.get("budget_json", {}),
            "report_available": state["status"] == "completed"
            and self._report_path(investigation_id).exists(),
            "created_at": state.get("created_at"),
        }

    def get_report(self, investigation_id: str) -> dict | None:
        p = self._report_path(investigation_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def get_report_text(self, investigation_id: str) -> str | None:
        p = self._report_txt_path(investigation_id)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def store_evidence(self, investigation_id: str, evidence: dict) -> None:
        with self._lock:
            if self._read_state(investigation_id) is None:
                raise KeyError(investigation_id)
            existing = self._evidence_path(investigation_id)
            if existing.exists() and json.loads(existing.read_text(encoding="utf-8")) != evidence:
                raise ValueError("Final evidence is immutable")
            self._write_atomic(self._evidence_path(investigation_id), json.dumps(evidence))

    def get_evidence(self, investigation_id: str) -> dict | None:
        p = self._evidence_path(investigation_id)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def approve_contract(self, project_id: str, contract_hash: str) -> dict:
        approval = {
            "approved": True,
            "contract_hash": contract_hash,
            "approved_at": datetime.now(UTC).isoformat(),
            "approved_by": "user",
        }
        with self._lock:
            existing = self.get_approval(project_id)
            if existing is not None:
                if existing["contract_hash"] != contract_hash:
                    raise ValueError("Approved contract hash is immutable")
                return existing
            self._write_atomic(self._approval_path(project_id), json.dumps(approval))
        return approval

    def get_approval(self, project_id: str) -> dict | None:
        p = self._approval_path(project_id)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def get_events(self, investigation_id: str) -> list[dict]:
        with self._lock:
            p = self._events_path(investigation_id)
            if not p.exists():
                return []
            lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
            return [json.loads(ln) for ln in lines]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_investigation_store() -> InvestigationStore:
    """
    Return a FileSystemInvestigationStore when persistence is configured.
    Fail loudly if its storage directory is not writable.
    """
    import os as _os

    from app.config import get_settings

    settings = get_settings()
    if settings.investigation_store == "postgres":
        from app.storage.postgres_investigation_store import PostgresInvestigationStore

        return PostgresInvestigationStore(settings.database_url)
    if settings.investigation_store != "auto":
        raise ValueError(f"Unknown INVESTIGATION_STORE: {settings.investigation_store}")
    persist = settings.persist_investigations or _os.environ.get(
        "PERSIST_INVESTIGATIONS", ""
    ).lower() in ("1", "true", "yes")
    if not persist:
        return MemoryInvestigationStore()

    artifact_dir = Path(settings.artifact_dir)
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        # quick write-test
        test_file = artifact_dir / ".write_test"
        test_file.write_text("ok")
        test_file.unlink()
        return FileSystemInvestigationStore(artifact_dir)
    except OSError as exc:
        raise RuntimeError(
            f"Persistent investigation storage is unavailable: {artifact_dir}"
        ) from exc
