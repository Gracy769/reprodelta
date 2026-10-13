"""One shared, conservative budget across every runner/model stage."""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime

from app.domain.types import BudgetConfig
from app.providers.runner import RunManifest, RunnerAdapter, RunResult


class BudgetExceeded(RuntimeError):
    pass


def reserve(
    usage: dict,
    limits: dict,
    *,
    runs: int = 0,
    model_calls: int = 0,
    cost_usd: float = 0,
    tokens: int = 0,
) -> dict:
    """Return a new ledger before dispatch; failed calls still consume reservations."""
    now = datetime.now(UTC)
    result = dict(usage)
    started = result.get("started_at", now.isoformat())
    if (now - datetime.fromisoformat(started)).total_seconds() >= limits.get(
        "max_wall_seconds", 3600
    ):
        raise BudgetExceeded("Investigation wall-time budget exhausted")
    for key, increment, maximum in (
        ("runs", runs, limits.get("max_runs", 200)),
        ("model_calls", model_calls, limits.get("max_llm_calls", 2)),
        ("reserved_cost_usd", cost_usd, limits.get("max_cost_usd", 0.1)),
        ("reserved_tokens", tokens, limits.get("max_model_tokens", 24000)),
    ):
        if increment < 0 or not math.isfinite(increment):
            raise ValueError("Invalid budget reservation")
        value = result.get(key, 0) + increment
        if value > maximum:
            raise BudgetExceeded(f"Investigation {key} budget exhausted")
        result[key] = value
    result["started_at"] = started
    return result


class BudgetLedger:
    def __init__(self, budget: BudgetConfig, store=None, investigation_id: str | None = None):
        self.limits = budget.model_dump()
        self.store, self.investigation_id = store, investigation_id
        self.usage: dict = {}
        self.lock = threading.Lock()

    def charge(self, **amounts) -> None:
        if self.store is not None:
            self.usage = self.store.reserve_budget(self.investigation_id, **amounts)
        else:
            with self.lock:
                self.usage = reserve(self.usage, self.limits, **amounts)

    def remaining_wall(self) -> int:
        started = self.usage.get("started_at", datetime.now(UTC).isoformat())
        remaining = (
            self.limits["max_wall_seconds"]
            - (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
        )
        if remaining < 1:
            raise BudgetExceeded("Investigation wall-time budget exhausted")
        return int(remaining)


class BudgetRunner(RunnerAdapter):
    def __init__(self, runner: RunnerAdapter, ledger: BudgetLedger):
        self.runner, self.ledger = runner, ledger

    def submit(self, manifest: RunManifest) -> RunResult:
        self.ledger.charge(runs=1)
        timeout = min(manifest.timeout_seconds, self.ledger.remaining_wall())
        return self.runner.submit(manifest.model_copy(update={"timeout_seconds": timeout}))
