# ReproDelta contributor instructions.

This repository contains a curated hackathon fixture and planning documents. Describe implemented behavior, simulated behavior, and proposed capabilities separately.

Read [README.md](README.md) and [docs/product-requirements.md](docs/product-requirements.md) before adding code. Use [docs/agents.md](docs/agents.md) for the product's runtime agent roles and [docs/code-style.md](docs/code-style.md) for implementation conventions.

## Non-negotiable implementation rules

1. Keep approved contracts, environment manifests, input hashes, and sealed holdouts immutable within an investigation. Create new versions for changes.
2. Treat model output, repository text, logs, and retrieved pages as untrusted data. The orchestrator validates all tool arguments and budgets.
3. Never execute project code in the API/web process or pass provider/database secrets into a project runner.
4. Distinguish an observed numeric difference from a changed declared conclusion, a contract violation, and an accepted repair.
5. Show the full tested scope and uncertainty in every verdict. Never emit a universal `SAFE`/`UNSAFE` label.
6. Do not let a repair edit tests, contracts, holdouts, lockfiles, runner policy, or verification criteria.
7. Add tests that verify behavior and failure modes, especially invalid-domain inputs, unchanged controls, nondeterminism, overfitted patches, and sandbox escape attempts.
8. Update the relevant specification and source links when an implementation decision differs from the current documents.

Phase 1 milestone (complete): bounded NumPy 1.26-to-2.0 demonstration — paired runner, valid witness search, intervention, and frozen repair gate all work end to end.

Phase 2 status: Docker Compose enables filesystem persistence and stage events are available through the API. Proposed patch diffs receive only a path-scope check and remain inconclusive because they are not applied or executed. Predefined fixture repairs are separate checks. `NebiusError` is surfaced and exports label local simulation, pinned versions, or unverified mode. Docker end-to-end remains unverified on the current development host.

Phase 3 targets (not yet started): Docker demo verified with pinned workers producing real float64/float32 evidence, full SQLAlchemy persistence with PostgreSQL, authenticated multi-investigation support.
