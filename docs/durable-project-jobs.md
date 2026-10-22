# Durable approved-project jobs

Sandbox attempts reserve their full timeout and estimated cost in Postgres
under a live lease **before** the E2B create call. A successful attempt
reconciles that reservation to observed sandbox seconds exactly once. A
worker crash, lost lease, or provider failure retains the uncertain upper
bound in the saved job instead of reporting zero usage. The maximum is a
conservative estimate, not provider billing evidence.

This queue and a separate worker process in `Dockerfile.project-worker` are
available for the approved-project pilot. The owner-facing start, plan, status
and cancellation endpoints exist, but no Railway worker service is deployed.
`E2B_PROJECT_RUNS_ENABLED` remains `false`. The curated investigation runner
and its report URLs are unchanged.

Operator-requested profile validation now uses the same leased table with a
separate `profile_validation` kind and an immutable profile/source/template
manifest. Validation is disabled by default through
`E2B_PROFILE_VALIDATION_ENABLED=false`. The separate worker executes the
owner-approved sample in both prepared environments and profile approval
requires the matching completed validation ID. Owners can cancel queued or
running validations. Owner investigation lists exclude validation jobs.

## Immutable job record

`ProjectRunSpec` binds one approved profile, exact PR/MR base and head commits,
source snapshot digest, contract hash, and old/new environment lock digests.
The same owner and idempotency key can create only one job; a second request
with different inputs fails. The queue stores this manifest in Postgres before
any worker can claim it. A completed result is stored as bounded JSON with a
canonical SHA-256 digest. Historical jobs without the digest remain readable.
Each sandbox attempt also updates owner-scoped `sandbox_seconds` and
`estimated_sandbox_cost_usd` under the current lease before the terminal write.
On provider or cleanup failure, the worker records the full reserved timeout as
a conservative upper bound and sets `usage_uncertain=true`; it does not claim
that amount was actually billed. A process killed inside an attempt may leave
an incomplete usage record and ends as `worker_lost`.

## State transitions

| From | To | Condition |
| --- | --- | --- |
| queued | running | A leased worker claims under the global queue lock. |
| queued | cancelled | The owner cancels before claim. |
| running | completed | The current lease is live, no cancellation is pending, and a bounded result exists. |
| running | failed | The current worker records a stable error code, or the lease expires. |
| running | cancelled | The current worker has stopped execution and confirms cancellation. |

An expired lease becomes `failed` with `worker_lost`; it is never retried
silently or reported as a negative finding. A stale worker cannot overwrite a
terminal state. Claiming permits at most one running job per owner and two
globally. Cancellation of a running job sets a request flag; the worker must
stop and destroy both sandboxes before marking it cancelled.

## Before a start endpoint is exposed

- Queue SQL, lease expiry, cancellation, owner isolation, restart visibility,
  idempotency and the profile approval gate have been exercised against an
  isolated local PostgreSQL 18 database. The guarded integration tests in
  `tests/integration/test_project_queue_postgres.py` run only when
  `REPRODELTA_TEST_DATABASE_URL` points to a localhost database whose name
  starts with `reprodelta_test`; they create and remove a unique test schema.
  A live Railway worker and concurrent claim stress test remain release gates.
- Add a separate Railway worker. It must recheck the PR revision, approved
  profile, source digest, environment digests, transfer consent, and budget
  after claim. The worker implementation does these checks and heartbeats,
  but it still needs a live service setup.
- Verify sandbox lifetime and recorded cost against live E2B. The browser offers
  a five-stage flow and bounded contract-valid search with repeat controls,
  but the independent public/private pilot and live worker gates remain.
  Keep setup errors separate from a tested negative finding.
