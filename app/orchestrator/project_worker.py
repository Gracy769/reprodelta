"""Leased, separate-process worker for operator-approved project runs.

The API stores an immutable job manifest. This process alone reads source-host
credentials and the E2B key. No credential is sent to a project sandbox.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
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
from app.domain.profile_validation import ProfileValidationSpec
from app.domain.project_candidates import candidate_inputs, feedback_candidates
from app.domain.project_eligibility import check_eligibility
from app.domain.project_profile import ProfileApproval, ProjectProfileDraft
from app.domain.project_run import ProjectRunSpec
from app.providers.e2b_runner import (
    EnvironmentBatchResult,
    ProjectAdapter,
    execute_environment,
    execute_environment_batch,
)
from app.storage.project_profiles import ProjectProfileStore
from app.storage.project_run_queue import ProjectRunQueue
from app.storage.project_snapshots import ProjectSnapshotStore

log = logging.getLogger(__name__)

SNAPSHOT_PURGE_INTERVAL_SECONDS = 60 * 60
SNAPSHOT_PURGE_RETRY_SECONDS = 60


class RunRejected(Exception):
    """An expected admission failure with a stable, non-sensitive error code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SnapshotJanitor:
    """Remove expired encrypted source snapshots while the worker is alive."""

    def __init__(self, store: ProjectSnapshotStore, *, clock=time.monotonic):
        self.store = store
        self.clock = clock
        self.next_due = 0.0

    def sweep_due(self) -> None:
        now = self.clock()
        if now < self.next_due:
            return
        try:
            deleted = self.store.purge_expired()
        except Exception:
            # A transient database failure must not stop job recovery. Retry soon.
            log.warning("Expired snapshot cleanup failed")
            self.next_due = now + SNAPSHOT_PURGE_RETRY_SECONDS
        else:
            log.info("Removed %d expired project source snapshots", deleted)
            self.next_due = now + SNAPSHOT_PURGE_INTERVAL_SECONDS


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


async def _fetch_validation_source(spec: ProfileValidationSpec, owner_id: str):
    token = await asyncio.to_thread(_saved_token, owner_id, spec.provider)
    source_id = (
        _github_repository(spec.repository)
        if spec.provider == "github"
        else _gitlab_project(spec.repository)
    )
    return await immutable_snapshot(
        _host_get, spec.provider, token, source_id, spec.source_sha
    )


def _stored_snapshot(owner_id: str, files: dict[str, bytes], expected_digest: str):
    settings = get_settings()
    if not settings.project_snapshot_key:
        raise RunRejected("snapshot_storage_unconfigured")
    store = ProjectSnapshotStore(settings.database_url, settings.project_snapshot_key)
    if store.save(owner_id, files) != expected_digest:
        raise RunRejected("snapshot_changed")
    restored = store.get(owner_id, expected_digest)
    if restored is None:
        raise RunRejected("snapshot_missing")
    return restored


def _input_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _run_metered(queue, job_id, lease, rate, runner, **kwargs):
    """Record observed usage or a conservative uncertain bound on failure."""
    attempt_id = queue.reserve_sandbox_attempt(
        job_id, lease, timeout_seconds=kwargs["timeout_seconds"], usd_per_second=rate,
    )
    if attempt_id is None:
        raise RunRejected("lease_lost")
    try:
        observed = runner(**kwargs)
    except Exception:
        # The reservation survives process death and also covers this failure.
        raise
    if not queue.settle_sandbox_attempt(
        job_id, lease, attempt_id, actual_seconds=observed.duration_seconds,
    ):
        raise RunRejected("lease_lost")
    return observed


def run_validation_job(
    job: dict,
    queue: ProjectRunQueue,
    profiles: ProjectProfileStore,
    *,
    execute=execute_environment,
    fetch_source=_fetch_validation_source,
    store_snapshot=_stored_snapshot,
) -> bool:
    """Validate a draft's approved sample before an operator can approve it."""
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
                abort.set()
                return

    watcher = threading.Thread(target=watch_lease, daemon=True)
    watcher.start()
    try:
        spec = ProfileValidationSpec.model_validate(job["spec_json"])
        started = time.monotonic()

        def cancelled() -> bool:
            return abort.is_set() or queue.cancellation_requested(job_id, lease)

        if not get_settings().e2b_profile_validation_enabled:
            raise RunRejected("validation_disabled")
        if cancelled():
            raise RunRejected("cancelled")
        profile = profiles.get(job["owner_id"], spec.profile_id)
        if profile is None or profile["status"] != "draft":
            raise RunRejected("profile_changed")
        draft = ProjectProfileDraft.model_validate(profile["draft_json"])
        if (
            draft.digest != spec.profile_digest
            or draft.contract_hash != spec.contract_hash
            or profile["profile_digest"] != spec.profile_digest
            or profile["contract_hash"] != spec.contract_hash
            or draft.provider != spec.provider
            or draft.repository != spec.repository
            or not draft.source_transfer_consent
        ):
            raise RunRejected("profile_changed")
        files, source_digest = asyncio.run(fetch_source(spec, job["owner_id"]))
        if cancelled():
            raise RunRejected("cancelled")
        files = store_snapshot(job["owner_id"], files, source_digest)
        if draft.dependency_path not in files:
            raise RunRejected("dependency_manifest_missing")
        adapter = ProjectAdapter(draft.adapter_module, draft.adapter_function)
        expected_input_sha256 = _input_digest(draft.sample_input)
        results = []
        for environment in (spec.old, spec.new):
            if cancelled():
                raise RunRejected("cancelled")
            spent = sum(item.duration_seconds for item in results)
            remaining = min(
                900,
                math.floor(spec.max_wall_seconds - (time.monotonic() - started)),
                math.floor(spec.max_sandbox_seconds - spent),
                math.floor((spec.max_cost_usd - spent * spec.sandbox_usd_per_second)
                           / spec.sandbox_usd_per_second),
            )
            if remaining < 1:
                raise RunRejected("wall_budget_exhausted")
            observed = _run_metered(queue, job_id, lease, spec.sandbox_usd_per_second, execute,
                files=files,
                input_json=draft.sample_input,
                adapter=adapter,
                template_id=environment.template_id,
                expected_numpy=environment.numpy_version,
                expected_lock_sha256=environment.lock_sha256,
                should_abort=cancelled,
                timeout_seconds=remaining,
            )
            if observed.source_sha256 != source_digest:
                raise RunRejected("snapshot_changed")
            if observed.version != environment.numpy_version:
                raise RunRejected("environment_result_mismatch")
            if observed.input_sha256 != expected_input_sha256:
                raise RunRejected("sample_result_mismatch")
            results.append(observed)
            spent = sum(item.duration_seconds for item in results)
            if spent > spec.max_sandbox_seconds:
                raise RunRejected("sandbox_budget_exhausted")
            if spent * spec.sandbox_usd_per_second > spec.max_cost_usd:
                raise RunRejected("cost_budget_exhausted")
        if cancelled():
            raise RunRejected("cancelled")
        if time.monotonic() - started > spec.max_wall_seconds:
            raise RunRejected("wall_budget_exhausted")
        old, new = [evaluate_output(item.output, draft.contract) for item in results]
        finding = {
            "kind": "profile_sample_validation",
            "sample_validated": True,
            "scope": "one approved sample input on the same immutable source commit",
            "source_sha": spec.source_sha,
            "source_sha256": source_digest,
            "profile_digest": spec.profile_digest,
            "contract_hash": spec.contract_hash,
            "input_sha256": results[0].input_sha256,
            "old": old,
            "new": new,
            "changed_conclusion": old["conclusion"] != new["conclusion"],
            "sandbox_seconds": round(spent, 3),
            "estimated_sandbox_cost_usd": round(spent * spec.sandbox_usd_per_second, 6),
            "sandbox_ids": [item.sandbox_id for item in results],
        }
        return queue.finish(job_id, lease, status="completed", result=finding)
    except RunRejected as exc:
        if exc.code == "cancelled":
            return queue.finish(job_id, lease, status="cancelled")
        return queue.finish(job_id, lease, status="failed", error_code=exc.code)
    except Exception:
        log.warning("Project validation job failed (%s)", job_id)
        if abort.is_set():
            return False
        return queue.finish(job_id, lease, status="failed", error_code="validation_error")
    finally:
        stop.set()
        watcher.join(timeout=2)


def run_claimed_job(
    job: dict,
    queue: ProjectRunQueue,
    profiles: ProjectProfileStore,
    *,
    execute_batch=execute_environment_batch,
    fetch_source=_fetch_verified_source,
    store_snapshot=_stored_snapshot,
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
        if spec.sandbox_usd_per_second <= 0 or spec.max_cost_usd <= 0:
            raise RunRejected("cost_unconfigured")
        if cancelled():
            raise RunRejected("cancelled")
        profile, draft, approval = _approved_inputs(spec, job["owner_id"], profiles)
        review, files = asyncio.run(fetch_source(spec, job["owner_id"]))
        if not check_eligibility(profile, review)["eligible"]:
            raise RunRejected("change_not_eligible")
        if cancelled():
            raise RunRejected("cancelled")
        files = store_snapshot(job["owner_id"], files, spec.source_sha256)
        adapter = ProjectAdapter(draft.adapter_module, draft.adapter_function)
        initial_budget = (
            max(1, (spec.candidate_budget + 1) // 2)
            if spec.candidate_strategy == "adaptive" else spec.candidate_budget
        )
        candidates = candidate_inputs(
            draft.contract, draft.sample_input,
            strategy="sweep" if spec.candidate_strategy == "adaptive" else spec.candidate_strategy,
            budget=initial_budget,
            seed=spec.candidate_seed,
        )
        runs: list[list[EnvironmentBatchResult]] = [[], []]
        phases = [candidates]
        for phase_index in range(2 if spec.candidate_strategy == "adaptive" and
                                 spec.candidate_budget > len(candidates) else 1):
            phase = phases[phase_index]
            phase_hashes = [_input_digest(value) for value in phase]
            execution_inputs = [value for candidate in phase for value in (candidate, candidate)]
            execution_hashes = [digest for digest in phase_hashes for _ in (0, 1)]
            for env_index, environment in enumerate((approval.old, approval.new)):
                if cancelled():
                    raise RunRejected("cancelled")
                elapsed = time.monotonic() - started
                spent_seconds = sum(item.duration_seconds for group in runs for item in group)
                remaining = min(
                    900,
                    math.floor(spec.max_wall_seconds - elapsed),
                    math.floor(spec.max_sandbox_seconds - spent_seconds),
                    math.floor(
                        (spec.max_cost_usd - spent_seconds * spec.sandbox_usd_per_second)
                        / spec.sandbox_usd_per_second
                    ),
                )
                if remaining < 1:
                    raise RunRejected("wall_budget_exhausted")
                observed = _run_metered(
                    queue, job_id, lease, spec.sandbox_usd_per_second,
                    execute_batch, inputs_json=execution_inputs,
                    files=files, adapter=adapter, template_id=environment.template_id,
                    expected_numpy=environment.numpy_version,
                    expected_lock_sha256=environment.lock_sha256,
                    should_abort=cancelled, timeout_seconds=remaining,
                )
                if observed.source_sha256 != spec.source_sha256:
                    raise RunRejected("snapshot_changed")
                if observed.version != environment.numpy_version:
                    raise RunRejected("environment_result_mismatch")
                if (observed.input_sha256 != execution_hashes
                        or len(observed.outputs) != len(execution_inputs)):
                    raise RunRejected("candidate_result_mismatch")
                runs[env_index].append(observed)
                spent_seconds = sum(item.duration_seconds for group in runs for item in group)
                if spent_seconds > spec.max_sandbox_seconds:
                    raise RunRejected("sandbox_budget_exhausted")
                if spent_seconds * spec.sandbox_usd_per_second > spec.max_cost_usd:
                    raise RunRejected("cost_budget_exhausted")
            if phase_index == 0 and spec.candidate_strategy == "adaptive" and spec.candidate_budget > len(candidates):
                measured = []
                unstable = False
                for index, candidate in enumerate(phase):
                    old = evaluate_output(runs[0][0].outputs[index * 2], draft.contract)
                    old_repeat = evaluate_output(runs[0][0].outputs[index * 2 + 1], draft.contract)
                    new = evaluate_output(runs[1][0].outputs[index * 2], draft.contract)
                    new_repeat = evaluate_output(runs[1][0].outputs[index * 2 + 1], draft.contract)
                    unstable |= not all((
                        old["conclusion"] == old_repeat["conclusion"],
                        new["conclusion"] == new_repeat["conclusion"],
                        numeric_matches(old["ratio"], old_repeat["ratio"], draft.contract),
                        numeric_matches(new["ratio"], new_repeat["ratio"], draft.contract),
                        old["dtype"] == old_repeat["dtype"],
                        new["dtype"] == new_repeat["dtype"],
                    ))
                    measured.append((candidate, old["ratio"], new["ratio"]))
                if unstable:
                    break
                next_phase = feedback_candidates(
                    draft.contract, draft.sample_input, measured,
                    budget=spec.candidate_budget - len(candidates), seed=spec.candidate_seed,
                )
                if not next_phase:
                    break
                candidates.extend(next_phase)
                phases.append(next_phase)
        expected_inputs = [_input_digest(value) for value in candidates]
        results = [EnvironmentBatchResult(
            version=group[0].version,
            sandbox_id=group[0].sandbox_id,
            duration_seconds=sum(item.duration_seconds for item in group),
            source_sha256=group[0].source_sha256,
            input_sha256=[digest for item in group for digest in item.input_sha256],
            outputs=[output for item in group for output in item.outputs],
            stderrs=[stderr for item in group for stderr in item.stderrs],
        ) for group in runs]
        if cancelled():
            raise RunRejected("cancelled")
        if time.monotonic() - started > spec.max_wall_seconds:
            raise RunRejected("wall_budget_exhausted")
        if spec.candidate_budget > 1:
            observations = []
            for index, input_digest in enumerate(expected_inputs):
                old = evaluate_output(results[0].outputs[index * 2], draft.contract)
                old_repeat = evaluate_output(results[0].outputs[index * 2 + 1], draft.contract)
                new = evaluate_output(results[1].outputs[index * 2], draft.contract)
                new_repeat = evaluate_output(results[1].outputs[index * 2 + 1], draft.contract)
                repeatable = all((
                    old["conclusion"] == old_repeat["conclusion"],
                    new["conclusion"] == new_repeat["conclusion"],
                    numeric_matches(old["ratio"], old_repeat["ratio"], draft.contract),
                    numeric_matches(new["ratio"], new_repeat["ratio"], draft.contract),
                    old["dtype"] == old_repeat["dtype"],
                    new["dtype"] == new_repeat["dtype"],
                ))
                observations.append({
                    "input_sha256": input_digest,
                    "old": old,
                    "new": new,
                    "old_repeat": old_repeat,
                    "new_repeat": new_repeat,
                    "repeatable": repeatable,
                    "changed_conclusion": repeatable and old["conclusion"] != new["conclusion"],
                    "measurement_matches_tolerance": numeric_matches(
                        old["ratio"], new["ratio"], draft.contract
                    ),
                })
            witness_index = next(
                (index for index, item in enumerate(observations) if item["changed_conclusion"]),
                None,
            )
            witness = observations[witness_index] if witness_index is not None else None
            inconclusive = any(not item["repeatable"] for item in observations)
            spent = sum(item.duration_seconds for item in results)
            finding = {
                "kind": "approved_project_search",
                "scope": f"{len(observations)} contract-valid inputs at one dependency-only PR revision",
                "strategy": spec.candidate_strategy,
                "seed": spec.candidate_seed,
                "candidate_budget": spec.candidate_budget,
                "attempted": len(observations),
                "observations": observations,
                "witness": witness,
                "witness_input": candidates[witness_index] if witness_index is not None else None,
                "changed_conclusion": witness is not None,
                "inconclusive": inconclusive,
                "source_sha256": spec.source_sha256,
                "base_sha": spec.base_sha,
                "head_sha": spec.head_sha,
                "contract_hash": spec.contract_hash,
                "environment_lock_sha256": [
                    spec.old_environment_lock_sha256, spec.new_environment_lock_sha256
                ],
                "sandbox_seconds": round(spent, 3),
                "estimated_sandbox_cost_usd": round(spent * spec.sandbox_usd_per_second, 6),
                "approved_cost_ceiling_usd": spec.max_cost_usd,
                "sandbox_ids": [item.sandbox_id for group in runs for item in group],
            }
            return queue.finish(job_id, lease, status="completed", result=finding)
        old = evaluate_output(results[0].outputs[0], draft.contract)
        old_repeat = evaluate_output(results[0].outputs[1], draft.contract)
        new = evaluate_output(results[1].outputs[0], draft.contract)
        new_repeat = evaluate_output(results[1].outputs[1], draft.contract)
        repeatable = all((
            old["conclusion"] == old_repeat["conclusion"],
            new["conclusion"] == new_repeat["conclusion"],
            numeric_matches(old["ratio"], old_repeat["ratio"], draft.contract),
            numeric_matches(new["ratio"], new_repeat["ratio"], draft.contract),
            old["dtype"] == old_repeat["dtype"],
            new["dtype"] == new_repeat["dtype"],
        ))
        finding = {
            "kind": "approved_project_sample",
            "scope": "one approved sample input at one dependency-only PR revision",
            "changed_conclusion": repeatable and old["conclusion"] != new["conclusion"],
            "witness_input": draft.sample_input if repeatable and old["conclusion"] != new["conclusion"] else None,
            "repeatable": repeatable,
            "inconclusive": not repeatable,
            "measurement_matches_tolerance": numeric_matches(
                old["ratio"], new["ratio"], draft.contract
            ),
            "old": old,
            "new": new,
            "old_repeat": old_repeat,
            "new_repeat": new_repeat,
            "input_sha256": expected_inputs[0],
            "source_sha256": spec.source_sha256,
            "base_sha": spec.base_sha,
            "head_sha": spec.head_sha,
            "contract_hash": spec.contract_hash,
            "environment_lock_sha256": [
                spec.old_environment_lock_sha256, spec.new_environment_lock_sha256
            ],
            "sandbox_seconds": round(sum(item.duration_seconds for item in results), 3),
            "estimated_sandbox_cost_usd": round(
                sum(item.duration_seconds for item in results) * spec.sandbox_usd_per_second, 6
            ),
            "approved_cost_ceiling_usd": spec.max_cost_usd,
            "sandbox_ids": [item.sandbox_id for group in runs for item in group],
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
    allowed_kinds = []
    if settings.e2b_project_runs_enabled:
        allowed_kinds.append("investigation")
    if settings.e2b_profile_validation_enabled:
        allowed_kinds.append("profile_validation")
    if not allowed_kinds:
        raise SystemExit("Project execution and validation are disabled")
    if not os.environ.get("E2B_API_KEY"):
        raise SystemExit("E2B_API_KEY is required for the project worker")
    if not settings.project_snapshot_key:
        raise SystemExit("PROJECT_SNAPSHOT_KEY is required for the project worker")
    queue = ProjectRunQueue(settings.database_url)
    profiles = ProjectProfileStore(settings.database_url)
    janitor = SnapshotJanitor(ProjectSnapshotStore(settings.database_url, settings.project_snapshot_key))
    worker_id = uuid.uuid4()
    kinds = tuple(allowed_kinds)
    presence_stop = threading.Event()

    def publish_presence() -> None:
        while not presence_stop.is_set():
            try:
                queue.worker_heartbeat(worker_id, kinds)
            except Exception:
                log.warning("Project worker heartbeat failed")
            presence_stop.wait(15)

    queue.worker_heartbeat(worker_id, kinds)
    presence = threading.Thread(target=publish_presence, daemon=True)
    presence.start()
    try:
        while True:
            janitor.sweep_due()
            job = queue.claim(allowed_kinds=kinds)
            if job is None:
                time.sleep(5)
                continue
            if job["job_kind"] == "profile_validation":
                run_validation_job(job, queue, profiles)
            else:
                run_claimed_job(job, queue, profiles)
    finally:
        presence_stop.set()
        presence.join(timeout=2)
        try:
            queue.remove_worker(worker_id)
        except Exception:
            log.warning("Project worker presence cleanup failed")


if __name__ == "__main__":
    main()
