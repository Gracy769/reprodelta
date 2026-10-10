# Code style and implementation conventions

**Status:** proposed team convention. No codebase exists yet, so these choices are intentionally narrow and may be revised by a recorded decision before implementation.

## Stack and layout

Use Python 3.12 for the API, controller, runner protocol, and evaluation harness. Keep the old/new NumPy installations inside separate pinned execution images rather than the API environment. A small TypeScript/React frontend may be used for the evidence workbench; avoid a frontend framework if the team cannot finish the core runner. Proposed structure:

```text
app/api/            HTTP routes and request validation
app/domain/         contract, verdict, budget, and state types
app/orchestrator/   search, reduction, intervention, verification
app/providers/      Nebius model and runner adapters
app/storage/        metadata and content-addressed artifacts
runner/             isolated execution entry point and trace capture
web/                UI, if implemented
tests/              unit, integration, adversarial, and evaluation fixtures
docs/               these specifications
```

Dependencies point inward: API/UI and providers depend on domain contracts; domain code does not import cloud SDKs, database clients, or web frameworks. Keep model prompts versioned and reviewable under `app/providers/prompts/`. Treat model response schemas as external input. Put numerical comparison policy in one module so every verdict uses identical semantics.

## Python conventions

- Type-check public functions and data structures. Use explicit domain names (`ApprovedContract`, `CandidateInput`, `PairedRun`, `VerificationResult`) rather than generic dictionaries at module boundaries.
- Format and lint with Ruff; use one configuration in `pyproject.toml`. Pin tools in a lockfile. Make static checks part of CI. Do not silently apply code transformations to submitted projects.
- Use immutable/frozen domain records for approved contracts, environment manifests, and sealed holdouts. Mutations create new versions.
- Use UTC timestamps internally with timezone-aware types; display local time in UI. Use SHA-256 digests over canonical byte representations for evidence identity.
- Represent finite numerical values, NaN, positive/negative infinity, dtype, shape, and units explicitly in serialization. JSON numbers alone cannot preserve all NumPy semantics.
- Make randomness explicit: pass a generator/seed; log the seed. Never call ambient global RNG inside search or tests.
- Use named tolerances and a domain-specific comparison function. Specify whether dtype differences matter. NumPy's `assert_allclose` compares with `atol + rtol * abs(desired)` and has special NaN and shape behavior; do not inherit defaults without a contract decision. [NumPy reference](https://numpy.org/doc/stable/reference/generated/numpy.testing.assert_allclose)
- Keep functions small around one reason to change. Separate candidate generation, validity checking, execution, comparison, explanation, and verdict assembly.
- Catch errors at service boundaries, attach a stable error code and evidence ID, and preserve the original exception in server logs. Never report a failed execution as a valid drift observation.

## API and schema conventions

Version external APIs and serialized artifacts (`schema_version`). Validate at entry, reject unknown fields for security-sensitive manifests, and document allowed state transitions. Use idempotency keys for job creation and cancellation. Paginate events and avoid returning full traces in list endpoints. Every API response involving a verdict includes `contract_hash`, environment digests, tested scope, and evidence references.

Proposed API resources, not implemented routes: `projects`, `investigations`, `contracts`, `runs`, `candidates`, `interventions`, `patches`, `verifications`, `reports`. Final route paths and request schemas must be written alongside implementation, then documented from code; do not treat this list as a deployed API reference.

## Model and runner adapters

Keep an interface for `propose_contract`, `choose_candidate`, `propose_intervention`, and `propose_patch`, each returning a validated structured proposal plus model provenance. The adapter may call NVIDIA Nemotron through Nebius Token Factory's documented OpenAI-compatible API. Do not hard-code a model identifier before checking current availability. [Nebius API reference](https://api.tokenfactory.nebius.com/docs)

Runner adapters expose only `submit(manifest)`, `status(job_id)`, `cancel(job_id)`, and `read_result(job_id)`. The manifest has explicit allowed entry point, artifact digests, resource limits, and environment image. Never construct a shell command by concatenating user input. Pass arguments as an array and keep project code inside the sandbox.

## Frontend conventions

Use semantic HTML, explicit labels, keyboard focus, and live status announcements. Keep measured evidence in typed data objects and presentation components free of verdict logic. Do not round persisted numerical values for display; format a view copy while offering exact values. A color token never substitutes for a text label. The [Design system](design-system.md) defines screens and language.

## Review checklist

For every change: identify which contract or invariant it affects; add the narrowest meaningful test; show deterministic replay for numerical behavior; check that model output cannot bypass validation; document a new schema version or migration if evidence shape changed; keep secrets and large data out of diffs. Use the [Testing plan](testing.md) for required gates and [Security](security.md) for untrusted-code paths.
