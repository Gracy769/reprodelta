# ReproDelta

ReproDelta investigates a narrow failure: a scientific Python workflow
still executes after a dependency upgrade, yet a conclusion the researcher
cares about changes. It replays immutable inputs in two environments, searches
for a valid counterexample, tests a suspected mechanism, and checks a proposed
repair against frozen independent cases.

> **Implementation status:** curated hackathon demo under development.
> - Docker Compose enables filesystem persistence in `ARTIFACT_DIR`; completed investigations, approvals, evidence, and reports survive an API restart. Local mode defaults to memory storage.
> - Interrupted investigations are never silently shown as completed.
> - Stage events are emitted at each step (preflight, controls, search, reduction, explanation, intervention, repair, report).
> - Model diffs targeting the curated pipeline are applied with exact hunks to ephemeral, hashed project copies inside pinned workers. The witness and sealed holdouts run before/after the patch in both environments. Unsupported syntax, execution errors or missing evidence cannot pass. Local simulation cannot execute model diffs. Predefined fixture checks remain separately labeled.
> - Mock LLM mode is explicit; `NebiusError` from live calls surfaces in investigation events.
> - Reports include `run_mode` (`local_simulation`, `pinned_workers`, or `unverified`) and a `simulated_mode` flag.
> - Docker remains unverified on this host — see **Docker** section.

## Correctness checks

Compatible scientific workflow extensions add executable input predicates,
measurement-preserving model-diff checks, native numerical checkpoint diagnostics,
opt-in adaptive search, portable recorded-regression tests and multi-seed
evaluation. The curated numerical workflow remains the supported run; arbitrary repository execution
is still unsupported. See [scope, usage and limits](docs/compatible-upgrades.md)
and the [reviewed plan](docs/compatible-upgrades-plan.md).

Paired output decisions are evaluated from the measured ratio using the approved
contract. Exact ties use its tie policy (`inconclusive` for this fixture), while
raw worker labels remain in paired output evidence. Numeric tolerances classify
numeric drift only; they do not move the decision threshold. A decisive outcome
changing to a tie is a changed declared outcome, not proof of a scientific bug.
Missing/invalid results and failed candidate executions cannot yield a negative
finding. Failed explanation, intervention and predefined repair stages are
recorded as events and exported in `stage_failures` and report limitations.
The curated evaluator supports the `ratio` measurement / `conclusion` selector;
other selectors and non-finite decision measurements fail explicitly.

Run the Python suite with `python -m pytest tests -q`. The rendering regression
checks use Node's built-in test runner: `node --test tests/frontend/*.cjs`.
These checks do not verify a live Nebius call or Docker deployment.

## Accounts and source connections

`/workspace` (also served at `/investigations/new` for existing links) accepts
a GitHub pull request or GitLab merge request URL, offers recent open changes
from up to eight recently updated connected projects, and provides repository
search as a fallback. **Review change** reads paginated file metadata and
supported manifests at immutable base/head commits. It shows observed package
constraint changes, source evidence, missing information and three next actions.
Reviews persist privately by owner and commit in Postgres; stale revisions fail
explicitly. Rate limits and permission failures are distinct.

**Check supported paired replay** verifies the exact source and dependency pins
before displaying the contract or accepting approval. It starts the existing engine only when both commits match the trusted numerical
kernel and the selected manifest pins NumPy 1.26.4 to 2.0.2. Other repositories
receive a setup explanation; no general user-code execution is exposed. See
[connected review and replay](docs/connected-review.md). The former standalone
investigation page has been removed; start from `/workspace`.

First-party GitHub and GitLab OAuth use a short-lived state challenge, an
HttpOnly session cookie, and server-side encrypted token storage in Postgres.
Account profiles persist a random generated identicon and display a masked
email in the header. GitHub users may need to sign in again to grant the added
`user:email` scope; an unavailable provider email is never invented.
Configure `APP_URL`, `SECRET_KEY`, and provider OAuth client variables to enable
them. GitHub OAuth Apps require the broad `repo` scope to show private repos;
ReproDelta itself makes read requests only. A fine-grained GitHub token remains
available when users prefer repository-limited access. GitLab requests
`read_api`. See [provider setup](docs/auth-provider-setup.md).

The existing managed Neon bridge can still supply email/password and Google
sign-in when `NEON_AUTH_BASE_URL`, `NEON_AUTH_JWKS_URL`, and an allowed origin
are configured for the public domain. Those options are not enabled by the new
Railway project's GitHub connection. Never commit OAuth secrets, source tokens,
Auth cookies, or `SECRET_KEY`. Rotating `SECRET_KEY` invalidates source sessions
and encrypted source connections.

## Quick start (Docker)

```sh
# 1. Copy env template and optionally add your Nebius API key
cp .env.example .env
# Leave NEBIUS_API_KEY blank to run in mock LLM mode (no key needed)

# 2. Build and start everything
docker compose up --build

# 3. Open the workbench
open http://localhost:8000
```

**Investigation flow:**

1. Open `http://localhost:8000/workspace` and sign in with a connected source.
2. Select or paste a PR/MR, then click **Review change** to inspect its diff.
3. The workspace checks the numerical kernel and NumPy pins. If eligible, review
   the exact commits, contract, budget and search strategy, then approve and start
   the paired run. If ineligible, it shows the reason without offering approval.
4. Watch live progress: controls → search → reduction → intervention → repair.
5. Inspect the evidence page — witness, model explanation, intervention result,
   and the predefined fixture repair checks
6. Download the evidence report (JSON bundle + text summary).

Connected paired runs use the configured model mode and the existing bounded
budget. The selected PR is checked against immutable revisions and supported
source and environment pins before the trusted fixture can execute. A stale
login offers a sign-in recovery path.

## Local development (no Docker)

```sh
# Install the package in editable mode with dev extras
pip install -e ".[dev]"

# Run all tests
python -m pytest tests/ -q

# Start the development server
python -m uvicorn app.main:app --reload --port 8000
# Open http://localhost:8000
```

## Railway

The Railway project is linked to the GitHub repository and deploys from `main`.
It uses the root Dockerfile, a Postgres service, and a generated public domain.
The Dockerfile runs the reviewed NumPy fixture in two pinned worker environments
and serves FastAPI on Railway's `PORT`. Set the application variables as described
in [Railway deployment](docs/railway-deployment.md). This release was deployed
with the Railway CLI after pushing to `main`; automatic GitHub redeployment has
not yet been verified. No local Docker installation is required. The Railway
workspace SSH key permits access to a running service; it does not authenticate
deployments.

## Previous Antideploy deployment

The interactive API reference at `/docs` and the alternate `/redoc` view
serve their JavaScript and CSS from this application. This keeps both pages
working under the site's same-origin Content Security Policy. The local copies
are [Swagger UI Dist 5.33.1](https://www.npmjs.com/package/swagger-ui-dist)
and [ReDoc 2.5.4](https://www.npmjs.com/package/redoc); their licenses are
included beside the assets in `app/static/api-docs/`. The project mark is a
small SVG at `app/static/favicon.svg`, also used in the browser tab.

The curated demo is configured for one Antideploy application at
<https://reprodelta-lab.antideploy.app>. The root `Dockerfile` starts two local HTTP
workers in separate Python 3.12 virtual environments, pinned to NumPy 1.26.4
and 2.0.2, and the FastAPI website on Antideploy's `PORT`. The workers run under
different unprivileged users with a minimal environment and listen on loopback.
This is process separation within one container, not the network isolation of
the Docker Compose setup; only the fixed, reviewed fixture is executed.

Antideploy provisions `DATABASE_URL` and `SECRET_KEY`. The app sets
`INVESTIGATION_STORE=postgres` in its Dockerfile and stores investigation
status, stage events, evidence, reports, and approvals in Postgres. It does not
use container disk for durable investigation data. The platform's database
variables are in the ignored local `.env` for development against that database;
do not commit the file or include it in a deployment archive.

To deploy a new version, follow <https://antideploy.com/agent.md> using the
application ID in `.antideploy.json`, and upload the project archive excluding
`.env`, `.git`, virtual environments, and caches. The Antideploy dashboard is
<https://antideploy.com/app/19229ee6-89a2-4fda-a94a-ef605175f84b>.

`NEBIUS_API_KEY` is deliberately absent from the deploy. Without it, the model
proposal is labeled mock mode. When a live model is ready, add the key as an
Antideploy application secret and redeploy. Keep the public demo's request
limits and model budget in mind before enabling a billable key.

### Run migrations manually

```sh
# Against the Docker database
docker compose run --rm api alembic upgrade head

# Or locally (set DATABASE_URL first)
alembic upgrade head
```

### Verify the NumPy behavior difference

```sh
python fixture/verify_difference.py
```

This script checks a precision simulation. Compose is configured to use actual NumPy
versions. To verify the fixture directly without Docker, run `fixture/project/run.py`
with the same JSON signal under NumPy 1.26.4 and 2.0.2.

## Environment variables

Copy `.env.example` to `.env` and edit as needed.

| Variable | Default | Description |
|---|---|---|
| `NEBIUS_API_KEY` | *(blank)* | Nebius Token Factory key. Blank = mock LLM mode. |
| `NEBIUS_MODEL_ID` | `nvidia/Nemotron-3-Ultra-550b-a55b` | Model to call. Check current availability at `https://api.tokenfactory.nebius.com/v1/models`. |
| `NEBIUS_BASE_URL` | `https://api.tokenfactory.nebius.com/v1` | Nebius API base URL. |
| `DATABASE_URL` | `postgresql+asyncpg://replay:replay@db:5432/replay` | PostgreSQL connection string. |
| `ARTIFACT_DIR` | `/data/artifacts` | Content-addressed artifact storage root. |
| `RUNNER_MODE` | `local` | `local` = in-process simulation; Compose overrides this to `worker_http` for actual paired workers. |
| `WORKER_OLD_CONTAINER` | `worker-old` | Docker container name for the NumPy 1.26 worker. |
| `WORKER_NEW_CONTAINER` | `worker-new` | Docker container name for the NumPy 2.0 worker. |
| `SECRET_KEY` | `change-me-in-production` | Application secret key. Change before any public deployment. |
| `PERSIST_INVESTIGATIONS` | `false` locally | Compose sets this to `true`; choose a writable `ARTIFACT_DIR` for local persistence. Startup fails if persistence is requested but storage is unavailable. |
| `INVESTIGATION_STORE` | `auto` | `auto` uses memory or filesystem persistence; `postgres` uses the provisioned Postgres database for the hosted demo. |
| `LOG_LEVEL` | `INFO` | Logging level. |

## Docker

Docker Compose is configured but was **not run or validated on this development host** (Docker is not installed). The worker Dockerfiles install pinned NumPy versions (1.26.4 and 2.0.2) and serve the `WorkerHttpRunnerAdapter` HTTP endpoint.

To verify Docker locally:

```sh
cp .env.example .env
docker compose up --build
# Open http://localhost:8000/workspace and select a supported PR/MR
# Verify the witness run shows intermediate_dtype=float64 on old worker
# and intermediate_dtype=float32 on new worker
```

Local runs are labeled `local_simulation`. Reports label the run mode `pinned_workers` only when both expected NumPy versions appear in the evidence; otherwise it is `unverified`.

## Test results

```sh
python -m pytest tests/ -q
# Run the full suite; see the current test output for the count.
```

Test coverage by layer:

| Layer | File(s) | What it proves |
|---|---|---|
| Domain unit | `test_domain`, `test_contract`, `test_validator` | Types, contract hash, input validity |
| Artifact | `test_artifacts` | SHA-256 round-trip, integrity check |
| Fixture | `test_fixture`, `test_witness` | Fixture logic and precision simulation |
| Runner | `test_runner` | Manifest validation, path-traversal rejection |
| Executor | `test_executor` | Paired run comparison, VerdictKind assignment |
| Search | `test_search` | Witness found, invalid candidates rejected, budget stops search |
| Reducer | `test_reducer` | Minimal witness confirmed, reduction steps recorded |
| Evidence | `test_evidence` | Evidence bundle construction |
| Nebius | `test_nebius` | Mock mode proposals, request digest format, NebiusError surfacing |
| Intervention | `test_intervention` | NEP-50 mechanism supported by explicit-float32 intervention |
| Repair | `test_repair` | Overfitted patch fails, good patch passes, holdout list immutable |
| Security | `test_security`, `test_prompt_injection` | Path traversal, extra manifest fields, injection in description |
| Evaluation | `test_evaluation_fixtures` | Unchanged controls, invalid rejection, overfitted patch, good patch, report fields |
| Report | `test_report` | Report JSON fields, text summary, replay instructions, run_mode flag |
| Persistence | `test_persistence` | Restart recovery, interrupt detection, event ordering, report survives restart |
| Proposed patch scope | `test_patch_verifier` | Rejects disallowed paths/syntax, applies exact hunks, rejects witness-only and no-op repairs, and never passes incomplete execution evidence |
| Worker failure | `test_worker_failure` | Timeout → setup_failure, budget exhausted stops search |
| Integration | `test_api_investigations`, `test_investigation_flow` | Full end-to-end API and investigation run |

## The demonstration fixture

The curated demonstration uses a signal classification pipeline
(`fixture/project/pipeline.py`) that exposes a documented NumPy 2.0 behavior
change.

**Mechanism (NEP 50 scalar promotion):**

- NumPy 1.26: each `float32_scalar / 2.0` → `float64` intermediate
- NumPy 2.0: each `float32_scalar / 2.0` → `float32` intermediate (no upcasting,
  NEP 50)

Near the classification threshold this precision difference flips the
conclusion.

**Known witness:**

```python
signal = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0000001192092896]
# Old env (float64): ratio = 0.50000001490…  → above_threshold
# New env (float32): ratio = 0.50000000000   → inconclusive (exact tie)
```

> **Label:** constructed demonstration case — not an independently observed
> research defect. See `fixture/project/pipeline.py`.

**Intervention:** replacing each `sample / 2.0` with `sample / np.float32(2.0)`
forces float32 arithmetic in both environments, making conclusions consistent.
The intervention assessment is `supported`.

**Repair gate:**
- Overfitted patch (special-cases the witness): **fails** on holdout #5
- Good patch (explicit `np.float32` divisor): **passes** all 6 holdouts

## Verdict vocabulary

The system uses exactly these bounded verdict classes (never a global
"SAFE"/"UNSAFE" badge):

| Verdict | Meaning |
|---|---|
| `observed_drift` | A numeric difference was measured |
| `supported_conclusion_change` | The declared conclusion changed across environments for a valid input |
| `pre_existing_contract_violation` | The input violated the contract before any upgrade |
| `repair_passes_stated_checks` | The proposed patch passed all frozen holdout checks |
| `no_violation_found` | No conclusion change found within the tested scope and budget |
| `inconclusive` | Evidence insufficient to support a narrower verdict |
| `setup_failure` | Runner, hash, or environment configuration problem |

## Security

- Workers run with no network, no database credentials, no Nebius key
- `RunManifest` rejects unknown fields (including `api_key`, `database_url`)
- Entry points are allowlisted — path traversal attempts are rejected
- Model proposals are untrusted data; deterministic validators make all decisions
- Holdouts are frozen before any patch generation call
- See `docs/security.md` for the full threat model

## Documentation

| Document | Purpose |
|---|---|
| [Product requirements](docs/product-requirements.md) | Users, scope, workflows, acceptance criteria |
| [Design system](docs/design-system.md) | Information architecture, visual tokens, accessibility |
| [Database](docs/database.md) | Data model, artifact storage, invariants |
| [Security](docs/security.md) | Threat model and controls |
| [Code style](docs/code-style.md) | Implementation conventions and module boundaries |
| [Testing](docs/testing.md) | Test strategy, evaluation plan, release gates |
| [Agents](docs/agents.md) | Runtime agent roles, tool contracts, stopping rules |
| [Research and decisions](docs/research-and-decisions.md) | Sources, prior work, open decisions |
| [Source architecture](docs/source-architecture.md) | Original research direction document |

## Shared vocabulary

- **Contract:** approved valid input domain, consequential output, comparison
  semantics, margin, and resource limits.
- **Witness:** a valid input that reproducibly changes the declared conclusion
  across environments.
- **Intervention:** one controlled change made to test a proposed mechanism.
- **Holdout:** verification inputs fixed before repair generation and hidden
  from the repair proposer.
- **Verdict:** a bounded statement about observations under the recorded
  contract and execution budget; never a universal safety claim.

## Hackathon notes

- Live Nebius/Nemotron calls require `NEBIUS_API_KEY` in `.env`. Without it
  the system runs in mock LLM mode and labels all proposals accordingly.
- The investigation uses a local in-process runner by default (`RUNNER_MODE=local`).
  Docker Compose sets `RUNNER_MODE=worker_http` to dispatch to the pinned workers.
- The repository is private until submission. The MIT license is included; make the repository public and
  make it public per the [hackathon rules](https://nebiusglobalaihackathon.devpost.com/rules)
  before submitting.

## Real model diff verification

The investigation seals a deep copy of the holdouts before model calls. The model receives the curated pipeline source, witness and intervention summary, but no holdouts. The controller validates the exact unified diff and computes base/patched project hashes. Each pinned worker repeats application and syntax validation, creates a fresh copy of `pipeline.py` and unchanged `run.py`, makes the files read-only, executes a child process and removes the copy. The original source is never patched.

The gate records 28 runs: original and patched witness plus six holdouts, each in NumPy 1.26.4 and 2.0.2. The original witness must reproduce a changed declared outcome; patched outcomes must agree. Holdouts with an already consistent conclusion must preserve it. Required dtype agreement is enforced. Failed execution, malformed output, version/hash mismatch or absent frozen cases yields incomplete evidence and cannot pass. A finite passing result means only `repair_passes_stated_checks`. Numerical tolerances still measure drift separately from the declared conclusion.

Only a restricted numerical subset in existing function bodies is executable: allowlisted NumPy/builtin calls, arithmetic, conditions and comprehensions over the bounded signal. Imports, module constants, signatures, attribute/input mutation, filesystem/network access, introspection and unbounded loops are rejected. This is a curated language restriction, not a general Python sandbox. Compose keeps its internal worker network and adds bounded writable `/tmp`; POSIX child processes enforce memory/CPU limits and inherit no provider/database secrets. Windows development subprocesses do not establish POSIX/container isolation.

`POST /api/investigations/{id}/verify-patch` requires stored witness evidence and uses that investigation's sealed holdouts and contract. Older evidence without sealed inputs remains inconclusive. The verification result is stored in an event; immutable final evidence is not overwritten. New investigation reports use schema version 3 and include the model diff result, hashes, actual run records, and sealed inputs.

To run the actual interpreter tests without Docker, provision two Python 3.12 environments containing `numpy==1.26.4` and `numpy==2.0.2`, set `REPRODELTA_OLD_PYTHON` and `REPRODELTA_NEW_PYTHON` to their interpreter paths, and run `python -m pytest tests/integration/test_real_repair.py -q`. `PinnedPythonRunnerAdapter` is an explicit development harness and is never selected by the public API. Real hosted execution uses `RUNNER_MODE=worker_http`; local simulation reports model diff verification as inconclusive.

## Ownership, budgets and deployment verification

Investigation APIs enforce the stored owner for state, events, streams, evidence, reports, execution and repair verification. Provider/Firebase sessions and bearer identities use the existing signature/expiry verifier. Guest demos receive a signed, HttpOnly, SameSite session with a separate owner; another guest cannot read their evidence. Contract approval is per owner. Invalid sessions fail closed, and cross-origin writes are rejected. Guest demos cannot start live model calls. Legacy rows with the old shared `user` owner are intentionally inaccessible through the public API; do not guess their owner.

Every runner stage shares an investigation ledger: controls, search, reduction, interventions, both predefined checks, executed model diffs and demonstration comparisons. Run/model-call/token/cost reservations happen before dispatch; failures retain reservations. API limits are immutable after creation, and ad-hoc exploration creates an owned 20-run investigation instead of using the unbudgeted `demo` alias. PostgreSQL reservations use row locks and persist across restarts; filesystem reservations are safe within the supported single API process. Wall deadlines persist from the first dispatch and constrain runner/model timeouts. Low-budget or incomplete comparison evidence is labeled explicitly.

Dollar reservations are operator-configured worst-case ceilings, not measured provider invoices. Live model calls fail closed unless `NEBIUS_CALL_COST_CEILING_USD` is positive; configure it from verified model pricing and request limits before enabling billable calls. The ledger also conservatively reserves UTF-8 prompt bytes plus the maximum output tokens. Reports retain provider-reported usage separately. Daily creation quotas and active-job caps now apply across investigations; provider billing reconciliation remains an operator responsibility. See [admission limits](docs/connected-review.md).

`/health` remains liveness. `/ready` checks session-key configuration, storage and both worker runtime versions/source hashes; it explicitly reports that external authentication and live Nebius were not probed. Worker `/health` exposes no secrets. Hosted/worker mode requires a stable secret of at least 32 characters. Deploy one API process/replica: the interruption detector does not yet support distributed leases. Compose API images now contain the fixture source used for hash validation.

GitHub CI provisions pinned interpreters and a dedicated PostgreSQL database, tests ownership/budget/restart behavior, and builds a disposable Docker Compose deployment in mock model mode. The Docker smoke check exercises real HTTP workers, actual model-diff execution, report export, owner isolation, completed-report recovery and interrupted-job recovery after API restart. It does not use live credentials or deploy to Railway.

## Demonstration comparison and replay

The second constructed case (`sensor_offset`) compares a calibrated float32 reading plus a small Python-float offset to 1.0. NumPy 1.26 and 2.0 produce different intermediate precision. The original power-ratio case, the sensor case and a clearly unchanged control are evaluated with fixed replay, seeded random valid search and an informed boundary sweep. Each strategy receives the same paired-execution cap and runs to that cap; first-witness position and every input/result are recorded. The informed sweep deliberately knows the boundary. A fourth case independently replays NumPy’s documented least-squares default change with a 4x2 matrix contract. These cases do not establish general superiority or scientific correctness; see [historical replay scope](docs/historical-replay.md).

`docs/evidence/demonstration.json` contains actual observations, source contents and hashes for a four-pair cap per strategy (72 executions total). `docs/evidence/replay.json` records an exact replay of all 72 executions; companion SHA-256 files cover the exports. Reproduce with two Python 3.12 environments containing NumPy 1.26.4/2.0.2:

```sh
python scripts/export_demonstration.py --old-python OLD --new-python NEW --pairs 4 --output demonstration.json
python scripts/export_demonstration.py --old-python OLD --new-python NEW --replay demonstration.json --output replay.json
```

The main investigation includes an eight-pair comparison per strategy when its shared budget permits. Evidence/report exports retain incomplete rows and their limitations. The sensor case currently supplies comparative replay evidence; the constrained model repair allowlist remains limited to the original power pipeline.
