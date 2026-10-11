# Security model

**Status:** proposed controls. Executing a user's repository is the highest-risk operation. The hackathon demo should accept only a curated repository until isolation is tested; a public upload feature is a separate security milestone.

## Assets and trust boundaries

Protect source code and datasets, Nebius credentials, model prompts/responses, artifact store, investigation evidence, and the integrity of verdicts. Trust boundaries are: browser → API; API → database/artifacts; API → Nemotron provider; API → execution runner; runner → project code and dependencies; retrieved documentation → agent context; repair proposal → verifier. User-supplied code, README files, logs, dependency output, web pages, and model output are **untrusted data** even when they look like instructions. OWASP recommends keeping authorization and tool policy outside the model. [OWASP prompt-injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)

## Threats and required controls

| Threat | Control | Release evidence |
| --- | --- | --- |
| Uploaded code reads credentials or other tenants' data | Run in isolated ephemeral workload; pass no Nebius/model/database secret to project code; mount only its immutable snapshot and scratch output; deny host mounts and privileged mode. | Malicious fixture cannot read test secret or adjacent project file. |
| Exfiltration through network or package hooks | Deny outbound network during execution, except a deliberately separated dependency-resolution phase against an allowlisted source. Pin and cache resolved wheels/images. | Network-attempt fixture is blocked and logged. |
| Container escape or resource exhaustion | Non-root runtime, capability drop, seccomp or equivalent provider isolation, CPU/memory/process/time/disk quotas, kill on cancellation. Confirm Nebius Job controls before production claims. | Stress fixture hits limits without affecting API. |
| Prompt injection in repo, logs, or retrieved docs | Label all external text as data; use narrow structured model outputs; validate tool arguments in code; agent cannot call arbitrary shell or widen scope. | Injection fixture cannot change tool policy or read holdouts. |
| Model proposes unsafe patch or changes tests | Permit edits only within allowlisted source paths; reject dependency, test, contract, runner, and holdout edits; compile/lint and independently verify. | Patch trying to weaken threshold/test is rejected. |
| Poisoned evidence or replay mismatch | Hash project, input, environment, traces, patch and report; append-only event log; compare identical input digests; record resolved dependency differences. | Tampered artifact fails verification. |
| Unauthorized investigation access | Authenticated ownership checks on every API and artifact fetch; signed short-lived download links; read-only demo tenant; avoid public-by-default artifacts. | Cross-tenant request returns denial. |
| Sensitive data sent to model or export | Default to curated synthetic data; redact known secrets and require user confirmation before sending private source/data to external inference; show data-flow notice. | Secret canary absent from model request/export. |

Docker's [rootless mode](https://docs.docker.com/engine/security/rootless/) and [seccomp guidance](https://docs.docker.com/engine/security/seccomp/) are useful implementation references, but a container alone is not proof of safe arbitrary-code execution. Use a provider-supported sandbox or isolated VM boundary if the public upload feature is enabled.

## Runner protocol

The orchestrator sends a signed job manifest containing only artifact digests, allowlisted entry point, finite budget, and run mode. The runner verifies the manifest, retrieves immutable artifacts, installs from pinned/cache-approved dependencies, executes as an unprivileged user, writes bounded results, and uploads them by digest. It must not expose a general shell endpoint to the model. The API treats stdout/stderr as inert text, never HTML or executable commands. Reject path traversal, symlinks escaping the snapshot, oversized files, archives with unsafe paths, and decompression bombs before execution.

Keep dependency download separate from the replay itself: dynamic package resolution can introduce confounds and network exposure. If the old/new environments necessarily differ beyond NumPy, list those packages and downgrade causal attribution to inconclusive until interventions isolate the effect.

## Model and tool policy

Nemotron may propose contracts, next candidate inputs, interventions, and patches. Deterministic validators decide input validity, tool authorization, budget, and verdict eligibility. The model never approves its own scientific property and never sees sealed holdout inputs. Tavily or other retrieved pages are optional hypothesis sources; they cannot override the approved contract or executable results. Store the page URL, fetch time, passage digest, and any licensing restrictions in the report.

## Data handling and operations

Store provider keys in a secret manager/environment injection for the API service, not in repository files, browser state, run artifacts, or prompts. Rotate compromised credentials. Encrypt transport and stored artifacts using provider controls. Define retention for investigations and temporary runner files; delete ephemeral execution storage on job termination. Allow the owner to delete investigations and derived artifacts, subject to shared-digest reference counts. Log access, execution, cancellation, and export without logging full secret-bearing payloads. Back up metadata and test restore procedures before accepting non-demo user data.

## Stop conditions

Stop and mark `setup failure` for missing/changed environment manifests, runner isolation failure, or failed hash verification. Stop and mark `inconclusive` when a required oracle or control cannot run, when old/old or new/new variation overwhelms the declared margin, or when several dependency changes remain confounded. Never convert these into “no regression.”

## Source-host connection boundary (implemented 11 October 2026)

The workspace authenticates the ReproDelta user through Neon Auth. The API
accepts only JWTs signed by the configured JWKS and checks issuer, audience,
expiry, and subject. GitHub and GitLab connections are indexed by that subject
in Postgres. Read tokens are encrypted at rest with a key derived from the
application `SECRET_KEY`; they never enter runner processes, model prompts,
browser storage, or API responses. Users can disconnect and delete the token.
This is an application-level boundary; a future production release should
rotate to a dedicated key-management service and audit connection access.

Source-host API bases are fixed in code, repository identifiers are validated,
redirects are disabled, and list sizes are bounded. Retrieved diff text is
untrusted data. The current advisor applies deterministic pattern checks only
and reports that no repository code was executed. See [GitHub's fine-grained
app permissions](https://docs.github.com/en/rest/apps/apps) and [GitLab's
projects API](https://docs.gitlab.com/api/projects/) for source-side access
controls. [Better Auth's cookie guidance](https://better-auth.com/docs/concepts/cookies)
notes that direct cross-site sessions can be affected by browser cookie
policies; a same-origin Auth proxy is a follow-up for broad browser support.
