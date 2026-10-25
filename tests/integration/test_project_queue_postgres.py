"""Real PostgreSQL queue checks; opt in with an isolated local test database.

Set REPRODELTA_TEST_DATABASE_URL to a localhost PostgreSQL database whose name
starts with reprodelta_test. Each run creates and drops only its own schema.
"""

import base64
import hashlib
import json
import os
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.domain.profile_validation import ProfileValidationSpec, pilot_lock_digest
from app.domain.project_profile import PreparedEnvironment, ProfileApproval
from app.domain.project_share import redacted_project_share
from app.storage.project_hypotheses import ProjectHypothesisStore
from app.storage.project_profiles import ProjectProfileStore
from app.storage.project_run_queue import ProjectRunQueue
from app.storage.project_shares import ProjectShareStore
from app.storage.project_snapshots import ProjectSnapshotStore
from tests.integration.test_project_profiles import _draft
from tests.unit.test_project_run_queue import _spec


def test_project_model_reservation_is_unique_and_owner_scoped(local_queue):
    queue, dsn = local_queue
    job = queue.submit("github:owner", "model-reservation-test", _spec())
    run_id = uuid.UUID(job["id"])
    store = ProjectHypothesisStore(dsn)
    summary = {"case": "bounded-observation", "measurement": 0.5}
    first, created = store.reserve("github:owner", run_id, summary=summary,
                                   model_id="nvidia/test", ceiling_usd=0.01)
    assert created and first["status"] == "pending"
    again, created = store.reserve("github:owner", run_id, summary=summary,
                                   model_id="nvidia/test", ceiling_usd=0.01)
    assert not created and again["id"] == first["id"]
    assert store.get("github:other", run_id) is None
    finished = store.complete("github:owner", run_id, request_digest="a" * 64,
                              response_text="Untested possibility", usage={"total_tokens": 10})
    assert finished["status"] == "completed"
    assert store.complete("github:owner", run_id, request_digest="b" * 64,
                          response_text="second", usage=None) is None


@pytest.fixture
def local_queue():
    raw = os.environ.get("REPRODELTA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("REPRODELTA_TEST_DATABASE_URL is not set")
    with psycopg.connect(raw, connect_timeout=5) as conn:
        if conn.info.host not in {"localhost", "127.0.0.1", "::1"}:
            pytest.skip("Queue integration requires a loopback PostgreSQL host")
        if not conn.info.dbname.startswith("reprodelta_test"):
            pytest.skip("Queue integration requires a reprodelta_test database")
        schema = "reprodelta_test_" + uuid.uuid4().hex
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped_dsn = make_conninfo(raw, options=f"-csearch_path={schema}")
    try:
        yield ProjectRunQueue(scoped_dsn), scoped_dsn
    finally:
        with psycopg.connect(raw, connect_timeout=5) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_postgres_queue_recovery_and_owner_isolation(local_queue):
    queue, dsn = local_queue
    spec = _spec()
    first = queue.submit("github:one", "first-job", spec)
    assert queue.submit("github:one", "first-job", spec)["id"] == first["id"]
    with pytest.raises(ValueError, match="different run"):
        queue.submit("github:one", "first-job", _spec())
    assert queue.get("github:two", uuid.UUID(first["id"])) is None
    claimed = queue.claim()
    assert claimed["id"] == first["id"] and claimed["status"] == "running"
    assert queue.claim() is None  # One active job per owner.
    first_attempt = queue.reserve_sandbox_attempt(
        uuid.UUID(first["id"]), uuid.UUID(claimed["lease_token"]),
        timeout_seconds=10, usd_per_second=0.0001,
    )
    assert first_attempt is not None
    assert queue.settle_sandbox_attempt(
        uuid.UUID(first["id"]), uuid.UUID(claimed["lease_token"]), first_attempt,
        actual_seconds=2.5,
    )
    assert queue.reserve_sandbox_attempt(
        uuid.UUID(first["id"]), uuid.UUID(claimed["lease_token"]),
        timeout_seconds=1, usd_per_second=0.0001,
    ) is not None
    assert queue.reserve_sandbox_attempt(
        uuid.UUID(first["id"]), uuid.uuid4(),
        timeout_seconds=1, usd_per_second=0.0001,
    ) is None
    with pytest.raises(ValueError, match="reservation"):
        queue.reserve_sandbox_attempt(
            uuid.UUID(first["id"]), uuid.UUID(claimed["lease_token"]),
            timeout_seconds=0, usd_per_second=0.0001,
        )
    restarted = ProjectRunQueue(dsn)
    running = restarted.get("github:one", uuid.UUID(first["id"]))
    assert running["status"] == "running"
    assert running["sandbox_seconds"] == 3.5
    assert running["estimated_sandbox_cost_usd"] == pytest.approx(0.00035)
    assert running["usage_uncertain"] is True
    assert not restarted.finish(
        uuid.UUID(first["id"]), uuid.uuid4(), status="completed", result={"ok": True}
    )
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE project_run_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
            (uuid.UUID(first["id"]),),
        )
    assert restarted.claim() is None
    lost = restarted.get("github:one", uuid.UUID(first["id"]))
    assert (lost["status"], lost["error_code"]) == ("failed", "worker_lost")
    assert not restarted.finish(
        uuid.UUID(first["id"]), uuid.UUID(claimed["lease_token"]),
        status="completed", result={"ok": True},
    )


def test_postgres_queue_cancellation_and_global_limit(local_queue):
    queue, _ = local_queue
    jobs = [queue.submit(f"github:owner-{n}", f"job-{n}-key", _spec()) for n in range(3)]
    first, second = queue.claim(), queue.claim()
    assert {first["id"], second["id"]} == {jobs[0]["id"], jobs[1]["id"]}
    assert queue.claim() is None  # Two active jobs globally.
    third_id = uuid.UUID(jobs[2]["id"])
    assert queue.cancel("github:owner-2", third_id)["status"] == "cancelled"
    assert queue.get("github:owner-2", third_id)["status"] == "cancelled"
    assert queue.cancel("github:owner-1", uuid.UUID(first["id"])) is None
    assert queue.cancel("github:owner-0", uuid.UUID(first["id"]))["status"] == "running"
    assert queue.cancellation_requested(
        uuid.UUID(first["id"]), uuid.UUID(first["lease_token"])
    )
    assert not queue.finish(
        uuid.UUID(first["id"]), uuid.UUID(first["lease_token"]),
        status="completed", result={"ok": True},
    )


def test_sandbox_reservation_survives_worker_loss_and_reconciles_only_once(local_queue):
    queue, dsn = local_queue
    job = queue.submit("github:one", "reserved-job", _spec())
    claimed = queue.claim()
    job_id, lease = uuid.UUID(job["id"]), uuid.UUID(claimed["lease_token"])
    attempt = queue.reserve_sandbox_attempt(
        job_id, lease, timeout_seconds=120, usd_per_second=0.000046,
    )
    assert attempt is not None
    restarted = ProjectRunQueue(dsn)
    reserved = restarted.get("github:one", job_id)
    assert reserved["sandbox_seconds"] == 120
    assert reserved["estimated_sandbox_cost_usd"] == pytest.approx(0.00552)
    assert reserved["usage_uncertain"] is True
    assert not restarted.settle_sandbox_attempt(
        job_id, uuid.uuid4(), attempt, actual_seconds=8,
    )
    assert restarted.settle_sandbox_attempt(job_id, lease, attempt, actual_seconds=8)
    assert not restarted.settle_sandbox_attempt(job_id, lease, attempt, actual_seconds=8)
    settled = restarted.get("github:one", job_id)
    assert settled["sandbox_seconds"] == pytest.approx(8)
    assert settled["estimated_sandbox_cost_usd"] == pytest.approx(0.000368)
    assert settled["usage_uncertain"] is False
    second = restarted.reserve_sandbox_attempt(
        job_id, lease, timeout_seconds=90, usd_per_second=0.000046,
    )
    assert second is not None
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE project_run_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
            (job_id,),
        )
    assert restarted.claim() is None
    lost = restarted.get("github:one", job_id)
    assert (lost["status"], lost["error_code"]) == ("failed", "worker_lost")
    assert lost["sandbox_seconds"] == pytest.approx(98)
    assert lost["estimated_sandbox_cost_usd"] == pytest.approx(0.004508)
    assert lost["usage_uncertain"] is True
    assert not restarted.settle_sandbox_attempt(job_id, lease, second, actual_seconds=9)


def test_postgres_approval_requires_matching_completed_validation(local_queue):
    queue, dsn = local_queue
    store = ProjectProfileStore(dsn)
    draft = _draft()
    profile = store.create("github:owner", draft)
    spec = ProfileValidationSpec(
        profile_id=uuid.UUID(profile["id"]), provider=draft.provider,
        repository=draft.repository, source_sha="a" * 40,
        profile_digest=draft.digest, contract_hash=draft.contract_hash,
        old=PreparedEnvironment(template_id="old", python_version="3.12",
                                numpy_version="1.26.4", lock_sha256=pilot_lock_digest("1.26.4")),
        new=PreparedEnvironment(template_id="new", python_version="3.12",
                                numpy_version="2.0.2", lock_sha256=pilot_lock_digest("2.0.2")),
        sandbox_usd_per_second=0.000046, max_cost_usd=0.1,
    )
    submitted = queue.submit_validation("github:owner", "validation-one", spec)
    assert [job["id"] for job in queue.list_validations("github:owner", uuid.UUID(profile["id"]))] == [submitted["id"]]
    assert queue.list_validations("github:other", uuid.UUID(profile["id"])) == []
    approval = ProfileApproval(
        profile_digest=draft.digest, validation_job_id=uuid.UUID(submitted["id"]),
        old=spec.old, new=spec.new, review_note="Reviewed both environments and adapter")
    with pytest.raises(ValueError, match="completed sample validation"):
        store.approve("github:owner", uuid.UUID(profile["id"]), "github:approver", approval)
    claimed = queue.claim(allowed_kinds=("profile_validation",))
    result = {
        "sample_validated": True, "profile_digest": draft.digest,
        "contract_hash": draft.contract_hash, "source_sha": spec.source_sha,
        "source_sha256": "b" * 64, "input_sha256": "c" * 64,
    }
    assert queue.finish(uuid.UUID(claimed["id"]), uuid.UUID(claimed["lease_token"]),
                        status="completed", result=result)
    saved_job = queue.get("github:owner", uuid.UUID(claimed["id"]), kind="profile_validation")
    expected_digest = hashlib.sha256(json.dumps(
        result, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    assert saved_job["result_sha256"] == expected_digest
    approved = store.approve(
        "github:owner", uuid.UUID(profile["id"]), "github:approver", approval)
    assert approved["status"] == "approved"
    assert approved["approval_json"]["validation_job_id"] == submitted["id"]


def test_postgres_snapshots_are_encrypted_owner_scoped_and_expire(local_queue):
    _, dsn = local_queue
    key = base64.urlsafe_b64encode(b"t" * 32).decode()
    store = ProjectSnapshotStore(dsn, key)
    files = {"package/adapter.py": b"private-marker-do-not-store-in-plaintext\n"}
    digest = store.save("github:owner", files)
    assert store.get("github:owner", digest) == files
    assert store.get("github:other", digest) is None
    with psycopg.connect(dsn) as conn:
        row = conn.execute("""SELECT nonce, ciphertext FROM project_source_snapshots
            WHERE owner_id=%s AND source_sha256=%s""", ("github:owner", digest)).fetchone()
        assert b"private-marker" not in bytes(row[1])
        assert bytes(row[0]) != bytes(row[1])[:12]
        conn.execute("""UPDATE project_source_snapshots SET expires_at=now()-interval '1 second'
            WHERE owner_id=%s AND source_sha256=%s""", ("github:owner", digest))
    assert store.get("github:owner", digest) is None
    assert store.purge_expired() == 1


def test_worker_presence_is_kind_scoped_and_expires(local_queue):
    queue, dsn = local_queue
    worker_id = uuid.uuid4()
    assert not queue.has_live_worker("investigation")
    queue.worker_heartbeat(worker_id, ("profile_validation",))
    assert queue.has_live_worker("profile_validation")
    assert not queue.has_live_worker("investigation")
    with psycopg.connect(dsn) as conn:
        conn.execute("""UPDATE project_run_workers
            SET last_seen=now()-interval '46 seconds' WHERE worker_id=%s""", (worker_id,))
    assert not queue.has_live_worker("profile_validation")
    queue.worker_heartbeat(worker_id, ("investigation",))
    assert queue.has_live_worker("investigation")
    assert not queue.has_live_worker("profile_validation")
    queue.remove_worker(worker_id)
    assert not queue.has_live_worker("investigation")


def test_oracle_worker_cannot_claim_e2b_job_or_advertise_e2b_presence(local_queue):
    queue, _ = local_queue
    e2b = queue.submit("github:e2b", "e2b-job", _spec())
    oracle = queue.submit(
        "github:oracle", "oracle-job",
        _spec().model_copy(update={"sandbox_provider": "oracle_docker"}),
    )
    worker_id = uuid.uuid4()
    capability = queue.capability("investigation", "oracle_docker")
    queue.worker_heartbeat(worker_id, (capability,))
    assert queue.has_live_worker(capability)
    assert not queue.has_live_worker("investigation")
    assert queue.claim(provider="oracle_docker")["id"] == oracle["id"]
    assert queue.claim(provider="e2b")["id"] == e2b["id"]


def test_postgres_operator_queue_tracks_only_pending_versions(local_queue):
    _, dsn = local_queue
    store = ProjectProfileStore(dsn)
    first = store.create("github:one", _draft())
    second = store.create("github:two", _draft())
    pending = store.list_pending()
    assert {row["id"] for row in pending} == {first["id"], second["id"]}
    assert store.list("github:one")[0]["id"] == first["id"]
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE project_profile_versions SET status='approved' WHERE id=%s",
            (uuid.UUID(first["id"]),),
        )
    assert [row["id"] for row in store.list_pending()] == [second["id"]]


def test_postgres_redacted_share_is_revocable_and_owner_scoped(local_queue):
    queue, dsn = local_queue
    shares = ProjectShareStore(dsn)
    submitted = queue.submit("github:owner", "share-test-key", _spec())
    job_id = uuid.UUID(submitted["id"])
    claimed = queue.claim()
    assert queue.finish(job_id, uuid.UUID(claimed["lease_token"]), status="completed", result={
        "kind": "approved_project_sample", "changed_conclusion": True,
        "old": {"ratio": 0.1}, "new": {"ratio": 0.9}, "input_sha256": "secret-input",
    })
    copy = redacted_project_share(queue.get("github:owner", job_id))
    with pytest.raises(ValueError, match="not found"):
        shares.create("github:other", job_id, copy)
    share = shares.create("github:owner", job_id, copy)
    assert shares.public(share["token"]) == copy
    assert shares.public("invalid") is None
    assert shares.list("github:other", job_id) == []
    assert not shares.revoke("github:other", job_id, uuid.UUID(share["id"]))
    assert shares.revoke("github:owner", job_id, uuid.UUID(share["id"]))
    assert shares.public(share["token"]) is None
