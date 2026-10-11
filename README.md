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
> - Proposed diffs are checked against a source-path allowlist. They are **not applied or executed yet**, so their verdict is inconclusive. The separate predefined repair fixtures exercise the holdout checks.
> - Mock LLM mode is explicit; `NebiusError` from live calls surfaces in investigation events.
> - Reports include `run_mode` (`local_simulation`, `pinned_workers`, or `unverified`) and a `simulated_mode` flag.
> - Docker remains unverified on this host — see **Docker** section.

## Accounts and source connections

`/investigations/new` is the signed-in source picker. Without a source session,
it offers GitHub and GitLab sign-in. With a source session, it lists accessible
repositories (including private, forked, and starred GitHub repositories), open
PRs or merge requests, and open issues. The fixed, executable NumPy contract is
at `/demo/investigation`. Selecting a repository only reads metadata and a
bounded diff: **Get scoped advice** checks at most 100 changed files and never
executes that repository or claims a replay verdict on it.

First-party GitHub and GitLab OAuth use a short-lived state challenge, an
HttpOnly session cookie, and server-side encrypted token storage in Postgres.
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

**Demo flow:**

1. Open `http://localhost:8000/demo/investigation`
2. Review the signal classifier contract → click **Approve and Start**
3. Watch live progress: controls → search → reduction → intervention → repair
4. Inspect the evidence page — witness, model explanation, intervention result,
   and the predefined fixture repair checks
5. Download the evidence report (JSON bundle + text summary)

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
| `NEBIUS_MODEL_ID` | `nvidia/Nemotron-3_5-Lightning` | Model to call. Check current availability at `https://api.tokenfactory.nebius.com/v1/models`. |
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
# Open http://localhost:8000 and run the demo investigation
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
| Proposed patch scope | `test_patch_verifier` | Rejects disallowed paths and never reports an unexecuted diff as a passing repair |
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
# New env (float32): ratio = 0.50000000000   → below_threshold
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
- The repository is private until submission. Add an open-source license and
  make it public per the [hackathon rules](https://nebiusglobalaihackathon.devpost.com/rules)
  before submitting.
