# Durable approved-project jobs

This queue is an internal foundation for the approved-project pilot. A separate
worker process now exists in `Dockerfile.project-worker`, but no API start
endpoint or Railway worker service is deployed. `E2B_PROJECT_RUNS_ENABLED`
remains `false`. The curated investigation runner and its report URLs are
unchanged.

## Immutable job record

`ProjectRunSpec` binds one approved profile, exact PR/MR base and head commits,
source snapshot digest, contract hash, and old/new environment lock digests.
The same owner and idempotency key can create only one job; a second request
with different inputs fails. The queue stores this manifest in Postgres before
any worker can claim it.

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

- Verify the queue SQL against an isolated Postgres test database and exercise
  concurrent claim, lease expiry, cancellation and restart recovery.
- Add a separate Railway worker. It must recheck the PR revision, approved
  profile, source digest, environment digests, transfer consent, and budget
  after claim. The worker implementation does these checks and heartbeats,
  but it still needs the isolated Postgres test and a live service setup.
- Persist bounded evidence and actual sandbox seconds, then implement owner
  status/progress endpoints and the five-stage browser flow. Keep setup errors
  separate from a tested negative finding.
