"""Single-API-process admission, with daily counts retained in durable stores."""
from __future__ import annotations

import threading
from datetime import UTC, datetime

from fastapi import HTTPException

from app.config import get_settings

_lock = threading.Lock()


def create(store, project_id, budget_json, owner_id):
    today = datetime.now(UTC).date().isoformat()
    with _lock:
        records = store.admission_records()
        daily = [r for r in records if str(r.get("created_at", "")).startswith(today)]
        drafts = [r for r in daily if r.get("status") == "draft"]
        if sum(r.get("owner_id") == owner_id for r in drafts) >= 10 or len(drafts) >= 1000:
            raise HTTPException(429, "Too many unstarted investigations; start an existing draft")
        return store.create_investigation(project_id, budget_json, owner_id)


def start(store, investigation_id, owner_id):
    settings = get_settings()
    today = datetime.now(UTC).date().isoformat()
    with _lock:
        records = store.admission_records()
        draft = next((r for r in records if r.get("id") == investigation_id), None)
        if draft is None or not str(draft.get("created_at", "")).startswith(today):
            raise HTTPException(409, "Draft expired; create a new investigation")
        started_today = [r for r in records if r.get("status") != "draft"
                         and str(r.get("created_at", "")).startswith(today)]
        limit = (settings.guest_daily_investigations if owner_id.startswith("guest:")
                 else settings.owner_daily_investigations)
        if (sum(r.get("owner_id") == owner_id for r in started_today) >= limit
                or len(started_today) >= settings.global_daily_investigations):
            raise HTTPException(429, "Daily investigation limit reached; retry tomorrow (UTC)",
                                headers={"Retry-After": "3600"})
        running = [r for r in records if r.get("status") == "running"]
        if len(running) >= settings.max_active_investigations or any(r.get("owner_id") == owner_id for r in running):
            raise HTTPException(429, "Investigation capacity is busy; wait for an active run to finish", headers={"Retry-After": "30"})
        store.transition_status(investigation_id, "running")
