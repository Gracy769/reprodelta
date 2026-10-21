"""Leased, separate-process worker for operator-approved project runs.

The API stores an immutable job manifest. This process alone reads source-host
credentials and the E2B key. No credential is sent to a project sandbox.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid

from app.api.connections import (
    _github_repository,
    _gitlab_project,
    _host_get,
    _host_pages,
    _saved_token,
)
from app.api.source_analysis import analyze, change_refs, immutable_snapshot
from app.config import get_settings
from app.domain.comparison import evaluate_output, numeric_matches
from app.domain.project_eligibility import check_eligibility
from app.domain.project_profile import ProfileApproval, ProjectProfileDraft
from app.domain.project_run import ProjectRunSpec
from app.providers.e2b_runner import ProjectAdapter, execute_environment
from app.storage.project_profiles import ProjectProfileStore
from app.storage.project_run_queue import ProjectRunQueue

log = logging.getLogger(__name__)


class RunRejected(Exception):
    """An expected admission failure with a stable, non-sensitive error code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def _fetch_verified_source(spec: ProjectRunSpec, owner_id: str):
    token = await asyncio.to_thread(_saved_token, owner_id, spec.provider)
    source_id = (
        _github_repository(spec.repository)
        if spec.provider == "github"
        else _gitlab_project(spec.repository)
    )
    review = await analyze(
        _host_get, _host_pages, spec.provider, token, source_id,
        spec.repository, spec.change_number,
    )
    if (review.get("base_sha"), review.get("head_sha")) != (spec.base_sha, spec.head_sha):
        raise RunRejected("revision_changed")
    files, digest = await immutable_snapshot(
        _host_get, spec.provider, token, source_id, spec.base_sha
    )
    if digest != spec.source_sha256:
        raise RunRejected("snapshot_changed")
    if await change_refs(_host_get, spec.provider, token, source_id, spec.change_number) != (
        spec.base_sha, spec.head_sha
    ):
        raise RunRejected("revision_changed")
    return review, files


def _approved_inputs(spec: ProjectRunSpec, owner_id: str, store: ProjectProfileStore):
    profile = store.get(owner_id, spec.profile_id)
    if profile is None or profile["status"] != "approved":
        raise RunRejected("profile_not_approved")
    if (
        profile["provider"] != spec.provider
        or profile["repository"] != spec.repository
        or profile["profile_digest"] != spec.profile_digest
        or profile["contract_hash"] != spec.contract_hash
    ):
        raise RunRejected("profile_changed")
    draft = ProjectProfileDraft.model_validate(profile["draft_json"])
    if draft.digest != spec.profile_digest or not draft.source_transfer_consent:
        raise RunRejected("profile_changed")
    approval = ProfileApproval.model_validate(profile["approval_json"])
    if (
        approval.profile_digest != draft.digest
        or approval.old.lock_sha256 != spec.old_environment_lock_sha256
        or approval.new.lock_sha256 != spec.new_environment_lock_sha256
    ):
        raise RunRejected("environment_changed")
    return profile, draft, approval


def run_claimed_job(
    job: dict,
    queue: ProjectRunQueue,
    profiles: ProjectProfileStore,
    *,
    execute=execute_environment,
    fetch_source=_fetch_verified_source,
) -> bool:
    """Run one claim. Return False if the lease was lost before a terminal write."""
    job_id = uuid.UUID(job["id"])
    lease = uuid.UUID(job["lease_token"])
    stop = threading.Event()
    abort = threading.Event()

    def watch_lease() -> None:
        while not stop.wait(20):
            try:
                if not queue.heartbeat(job_id, lease):
                    abort.set()
                    return
            except Exception:
                # A lost database connection cannot be assumed to preserve the
                # lease; stop project execution and let expiry mark worker_lost.
                abort.set()
                return

    watcher = threading.Thread(target=watch_lease, daemon=True)
    watcher.start()
    try:
        spec = ProjectRunSpec.model_validate(job["spec_json"])
        started = time.monotonic()

        def cancelled() -> bool:
            return abort.is_set() or queue.cancellation_requested(job_id, lease)

        if not get_settings().e2b_project_runs_enabled:
            raise RunRejected("execution_disabled")
        if cancelled():
            raise RunRejected("cancelled")
        profile, draft, approval = _approved_inputs(spec, job["owner_id"], profiles)
        review, files = asyncio.run(fetch_source(spec, job["owner_id"]))
        if not check_eligibility(profile, review)["eligible"]:
            raise RunRejected("change_not_eligible")
        if cancelled():
            raise RunRejected("cancelled")
        adapter = ProjectAdapter(draft.adapter_module, draft.adapter_function)
        results = []
        for environment in (approval.old, approval.new):
            if cancelled():
                raise RunRejected("cancelled")
            if time.monotonic() - started >= spec.max_wall_seconds:
                raise RunRejected("wall_budget_exhausted")
            observed = execute(
                files=files,
                input_json=draft.sample_input,
                adapter=adapter,
                template_id=environment.template_id,
                expected_numpy=environment.numpy_version,
                should_abort=cancelled,
            )
            if observed.source_sha256 != spec.source_sha256:
                raise RunRejected("snapshot_changed")
            results.append(observed)
            if sum(item.duration_seconds for item in results) > spec.max_sandbox_seconds:
                raise RunRejected("sandbox_budget_exhausted")
        if cancelled():
            raise RunRejected("cancelled")
        if time.monotonic() - started > spec.max_wall_seconds:
            raise RunRejected("wall_budget_exhausted")
        old, new = [evaluate_output(item.output, draft.contract) for item in results]
        finding = {
            "kind": "approved_project_sample",
            "scope": "one approved sample input at one dependency-only PR revision",
            "changed_conclusion": old["conclusion"] != new["conclusion"],
            "measurement_matches_tolerance": numeric_matches(
                old["ratio"], new["ratio"], draft.contract
            ),
            "old": old,
            "new": new,
            "input_sha256": results[0].input_sha256,
            "source_sha256": spec.source_sha256,
            "base_sha": spec.base_sha,
            "head_sha": spec.head_sha,
            "contract_hash": spec.contract_hash,
            "environment_lock_sha256": [
                spec.old_environment_lock_sha256, spec.new_environment_lock_sha256
            ],
            "sandbox_seconds": round(sum(item.duration_seconds for item in results), 3),
            "sandbox_ids": [item.sandbox_id for item in results],
        }
        return queue.finish(job_id, lease, status="completed", result=finding)
    except RunRejected as exc:
        if exc.code == "cancelled":
            return queue.finish(job_id, lease, status="cancelled")
        return queue.finish(job_id, lease, status="failed", error_code=exc.code)
    except Exception:
        # Provider, SDK and repository errors may include private URLs or
        # credentials. Log a type only and store a stable generic code.
        log.warning("Approved project job failed (%s)", job_id)
        if abort.is_set():
            return False
        return queue.finish(job_id, lease, status="failed", error_code="execution_error")
    finally:
        stop.set()
        watcher.join(timeout=2)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    settings = get_settings()
    if not settings.e2b_project_runs_enabled:
        raise SystemExit("E2B_PROJECT_RUNS_ENABLED is false; refusing to start the project worker")
    if not os.environ.get("E2B_API_KEY"):
        raise SystemExit("E2B_API_KEY is required for the project worker")
    queue = ProjectRunQueue(settings.database_url)
    profiles = ProjectProfileStore(settings.database_url)
    while True:
        job = queue.claim()
        if job is None:
            time.sleep(5)
            continue
        run_claimed_job(job, queue, profiles)


if __name__ == "__main__":
    main()
