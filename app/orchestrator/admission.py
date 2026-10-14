"""Single-API-process admission, with daily counts retained in durable stores."""
from __future__ import annotations

import threading
from datetime import UTC, datetime

from fastapi import HTTPException

from app.config import get_settings

_lock = threading.Lock()


def create(store, project_id, budget_json, owner_id):
    settings = get_settings()
    today = datetime.now(UTC).date().isoformat()
    with _lock:
        records = store.admission_records()
        daily = [r for r in records if str(r.get("created_at", "")).startswith(today)]
        per_owner = sum(r.get("owner_id") == owner_id for r in daily)
        limit = settings.guest_daily_investigations if owner_id.startswith("guest:") else settings.owner_daily_investigations
        if per_owner >= limit or len(daily) >= settings.global_daily_investigations:
            raise HTTPException(429, "Daily investigation limit reached; retry tomorrow (UTC)", headers={"Retry-After": "3600"})
        return store.create_investigation(project_id, budget_json, owner_id)


def start(store, investigation_id, owner_id):
    settings = get_settings()
    with _lock:
        running = [r for r in store.admission_records() if r.get("status") == "running"]
        if len(running) >= settings.max_active_investigations or any(r.get("owner_id") == owner_id for r in running):
            raise HTTPException(429, "Investigation capacity is busy; wait for an active run to finish", headers={"Retry-After": "30"})
        store.transition_status(investigation_id, "running")
