# Approved-project pilot

This is the first implementation slice of the approved-project roadmap. The
existing curated NumPy investigation remains the only production execution
path. `E2B_PROJECT_RUNS_ENABLED` defaults to `false`; profile records and
eligibility checks cannot start an E2B run.

## Current pilot boundary

- A signed-in owner can create versioned project profiles through
  `/api/project-profiles`. The draft records a source repository, Python
  module/function adapter, bounded sample input, numeric contract and explicit
  consent to transfer a selected source snapshot to E2B.
- A configured operator subject can record approval with exact old/new template
  IDs and lock digests. Template preparation and sample validation are not yet
  automated. Do not approve real projects until both environments and the
  adapter have been reviewed and the security gate passes.
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
  only bounded source and input bytes, verifies the installed NumPy version,
  and destroys the sandbox after success or failure. The standalone
  `scripts/e2b_smoke.py` checks a short local command and blocked public
  internet. It never uploads a repository or service credential.

## Release gate before enabling project runs

1. Build and verify prepared old/new E2B templates with exact lock digests.
2. Add preapproval sample validation, source snapshot persistence by digest,
   immutable run records and explicit private-source transfer notice.
3. Move starts to a leased Postgres worker with idempotency, cancellation,
   heartbeat and restart recovery. Enforce owner/global concurrency, 15-minute
   sandbox lifetime, and cost accounting.
4. Wire the five-stage browser flow and evidence ladder. Keep unsupported
   changes labelled “Diff review only.”
5. Pass malicious-source, network, timeout, cleanup, tenant isolation and
   budget-exhaustion checks, then run one independent public and one private
   approved project before pilot rollout.

The E2B key belongs only in Railway environment variables or a local secret
store. Never add it to source, logs, browser JavaScript or profile records.
