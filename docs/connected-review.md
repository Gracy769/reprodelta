# Connected dependency review and supported replay

The workspace's **Analyze changed files** action produces a private diff review,
never an execution verdict. It fetches the exact base/head commits, paginates
changed files (up to the host's 3,000-file GitHub limit), and reads at most ten
supported manifests of at most 128 KB each at those commits. Missing, oversized,
unparsed or truncated evidence is explicit. A PR updated during retrieval yields
409 rather than a result combining revisions.

Requirements, project/Poetry TOML, Poetry/uv locks, Pipfile and Pipfile.lock are
supported. Environment YAML receives only a conservative simple-list scan.
Declarations, optional groups and markers are not a resolved environment; the
review never treats package mentions or comment-only edits as confirmed upgrades.
Observed package changes link to immutable source files. Numerical keywords are
separately labeled hypotheses. No private source is sent to a model.

Reviews persist in `source_reviews` in the existing Postgres database, indexed
by owner/provider/repository/change/base/head. `/api/connections/reviews` lists
only the signed-in owner's last twenty records. A saved review stays labeled
with its historical SHA; replay rechecks the latest revisions. Connectors need
read access to PRs and repository contents for private manifests. GitHub API
rate limits, permission denial, expired connections and unavailable repositories
have distinct errors. Recent-change discovery reports partial failures.

## Configure a supported paired investigation

The separately approved replay is deliberately narrow:

1. Both remote revisions must contain `fixture/project/pipeline.py` identical to
   the trusted supported numerical source after canonical UTF-8/LF normalization.
2. The selected manifest must unambiguously pin `numpy==1.26.4` at the base and
   `numpy==2.0.2` at the head. Missing, ambiguous, optional, conditional or
   unresolved pins fail closed; a declaration guarded by an environment marker
   cannot authorize a replay on its own.
3. The user reviews and approves the existing eight-sample float32 contract,
   threshold/tie policy and execution/model budget. The submitted contract hash
   must match the current supported contract.
4. The deployment must use pinned HTTP workers. Start verifies `/ready` before
   launching any execution, and normal manifests enforce the trusted project
   hash as well as repair manifests. Local simulation cannot start
   this connected replay. Remote code is never imported or executed; only the
   trusted source-identical numerical kernel runs in the existing workers.
5. A verified bearer session is bridged to a signed, HttpOnly, SameSite=Strict
   30-minute workbench cookie so report navigation keeps the same owner. Returning
   to the workspace renews it; sign-out clears it.
6. The investigation's append-only `source_binding` event and exported report
   retain both commit SHAs, canonical source hashes, dependency path, versions
   and explicit scope. No conclusion covers other files or the full PR.

This supported adapter addresses the missing connection between an eligible PR
and a real paired engine. A general scientific Python project still needs its
own approved entry point, contract, resolved environments, immutable snapshot and
validated execution isolation. Rejecting an unsupported project is an expected
setup outcome, not a passing regression check. No generic shell or code-upload
endpoint is exposed.

## Admission and deployment

One API process/replica remains required. Admission serializes creation and start
within that process, reads durable creation metadata for daily quotas, and caps
active jobs before launching a thread. Defaults: two globally active jobs, one
per owner; ten guest creations/day, twenty authenticated creations/day and two
hundred total/day (UTC). Configure `MAX_ACTIVE_INVESTIGATIONS`,
`GUEST_DAILY_INVESTIGATIONS`, `OWNER_DAILY_INVESTIGATIONS` and
`GLOBAL_DAILY_INVESTIGATIONS`. Full investigations retain their shared execution,
wall-time and model budgets. Daily accounting survives filesystem/Postgres
restart; memory mode intentionally does not persist. Guest identity renewal can
bypass a per-guest cap, so the global cap is essential; production abuse controls
and distributed leases are separate work.

Use `/ready` to verify storage and worker versions/hashes. CI uses disposable
Postgres, pinned interpreters and Docker restart tests. Real provider sign-in,
Railway rollout/restart and billable Nebius calls require operator credentials;
`/ready` explicitly does not prove them. Revoke the API key previously disclosed
in chat and provision its replacement through the deployment secret settings.

Sources: [GitHub PR files and pagination](https://docs.github.com/en/rest/pulls/pulls),
[GitLab merge request diffs](https://docs.gitlab.com/api/merge_requests/).
