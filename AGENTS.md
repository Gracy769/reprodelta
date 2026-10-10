# Agent Replay Lab contributor instructions

This repository currently contains **planning documents only**. Do not describe proposed capabilities as implemented or measured.

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

The first milestone is the bounded NumPy 1.26-to-2.0 demonstration in [docs/testing.md](docs/testing.md). Stop feature expansion until the paired runner, valid witness search, intervention, and frozen repair gate work end to end.
