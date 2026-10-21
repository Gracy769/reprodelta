# Approved-project pilot

This is the internal implementation of the approved-project pilot. The
existing curated NumPy investigation remains the only production execution
path. `E2B_PROJECT_RUNS_ENABLED` defaults to `false`; the project-run start
endpoint returns 503 until the release gate is passed and the operator enables
it on both the API and the separate worker service.

## Current pilot boundary

- A signed-in owner can create versioned project profiles through
  `/api/project-profiles`. The draft records a source repository, Python
  module/function adapter, bounded sample input, numeric contract and explicit
  consent to transfer a selected source snapshot to E2B.
- A configured operator subject can queue sample validation against one immutable
  source commit and two fixed NumPy pilot templates. The separate E2B worker
  rechecks the draft and consent, executes the sample in both templates, and
  records bounded observations and cost. Approval requires that exact completed
  validation job ID and matching profile, contract, and environment digests.
  `E2B_PROFILE_VALIDATION_ENABLED` defaults to `false` on the API and worker.
  This preapproval run does not open the broader project-run gate.
- `/api/project-profiles/{id}/changes/{number}/eligibility` checks a complete
  PR/MR diff against one approved profile. It permits only one in-place edit to
  the approved dependency file, with exact NumPy before/after pins. The result
  is advice, not an execution approval; revision and source must be rechecked
  at run start.
- `immutable_snapshot` reads at most 200 regular files, 10 MB total, by commit
  SHA from the source-host API. It rejects truncated trees, symlinks, submodules,
  oversized files and Git blob mismatches. The source token remains in the web
  process and is never copied into the sandbox.
- The E2B adapter creates one secure sandbox with internet disabled, uploads
  only bounded source and input bytes, verifies the installed NumPy version and
  approved template lock digest before any source upload,
  and destroys the sandbox after success or failure. The standalone
  `scripts/e2b_smoke.py` checks a short local command and blocked public
  internet. It never uploads a repository or service credential.
- `/api/project-runs/profiles/{id}/changes/{number}/plan` shows the exact
  revision, contract, prepared environments, source transfer notice and
  configured cost ceiling. The owner must acknowledge those exact values in
  `POST /api/project-runs`; status and cancellation are owner-scoped. The
  worker rechecks all immutable inputs after claim and records only a bounded
  one-sample result. A single sample is not a general regression verdict.
- `project_candidates.candidate_inputs` can prepare up to 32 unique, contract-
  checked numeric scalar or one-dimensional array inputs by sweep or seeded
  random search. The E2B adapter can execute a bounded batch in one offline
  sandbox per environment, verifying the template before uploading source and
  destroying it on exit. The project worker still invokes the one-sample path
  until a shared execution/cost ledger is wired and verified; no broader search
  result is claimed yet.

## Release gate before enabling project runs

1. Build and verify prepared old/new E2B templates with exact lock digests.
   `python scripts/build_e2b_templates.py` previews the fixed NumPy lock
   digests; `--build` creates the two E2B images after operator review. The
   resulting template IDs and hashes must be recorded in profile approval.
   The fixed template build and synthetic live proof are recorded in
   [pilot E2B template proof](pilot-e2b-template-proof.md).
2. Verify the preapproval sample-validation worker on an isolated Postgres queue
   and on two reviewed projects. Source snapshots are fetched by immutable commit
   and hashed before upload. The pilot snapshot store encrypts source per owner
   under `PROJECT_SNAPSHOT_KEY`, addresses it by digest and expires it after seven
   days. Wire the worker to this store and verify deletion before enabling private
   projects. Show explicit private-source transfer consent.
3. Verify the leased Postgres queue and worker in an isolated database and
   deploy the separate Railway worker. Set a verified
   `E2B_SANDBOX_USD_PER_SECOND` and `E2B_PROJECT_RUN_COST_CEILING_USD` before
   enabling starts. Test cancellation, heartbeat and restart recovery against
   actual sandboxes. See [durable jobs](durable-project-jobs.md).
4. Wire the five-stage browser flow and evidence ladder. Keep unsupported
   changes labelled “Diff review only.”
5. Pass malicious-source, network, timeout, cleanup, tenant isolation and
   budget-exhaustion checks, then run one independent public and one private
   approved project before pilot rollout.

The E2B key belongs only in Railway environment variables or a local secret
store. Never add it to source, logs, browser JavaScript or profile records.
`PROJECT_SNAPSHOT_KEY` is a separate random 32-byte key encoded with URL-safe
base64 and belongs only in the worker's secret store. Losing or rotating it
without a key migration makes retained snapshots unreadable; no source snapshot
is stored unless this key is configured in the worker.
