# ReproDelta

ReproDelta investigates a narrow failure: a scientific Python workflow
still executes after a dependency upgrade, yet a conclusion the researcher
cares about changes. It replays immutable inputs in two environments, searches
for a valid counterexample, tests a suspected mechanism, and checks a proposed
repair against frozen independent cases.

> **Implementation status:** hackathon fixture. Docker runs the curated input in
> real NumPy 1.26.4 and 2.0.2 workers. Local mode simulates their precision
> difference for development. Investigation and approval state is in memory.

The model proposes an explanation and patch text, but this demo verifies two
predefined fixture repair variants. It does not apply arbitrary model patches.
PostgreSQL models and migrations are present; the API does not yet persist
investigations or approvals, so restarting the API clears them.

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

1. Open `http://localhost:8000` → click **Start Demo Investigation**
2. Review the signal classifier contract → click **Approve and Start**
3. Watch live progress: controls → search → reduction → intervention → repair
4. Inspect the evidence page — witness, model explanation, intervention result,
   patch verification matrix
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

This script checks a precision simulation. The Docker demo uses actual NumPy
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
| `LOG_LEVEL` | `INFO` | Logging level. |

## Test results

```sh
python -m pytest tests/ -q
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
| Nebius | `test_nebius` | Mock mode proposals, request digest format |
| Intervention | `test_intervention` | NEP-50 mechanism supported by explicit-float32 intervention |
| Repair | `test_repair` | Overfitted patch fails, good patch passes, holdout list immutable |
| Security | `test_security`, `test_prompt_injection` | Path traversal, extra manifest fields, injection in description |
| Evaluation | `test_evaluation_fixtures` | Unchanged controls, invalid rejection, overfitted patch, good patch, report fields |
| Report | `test_report` | Report JSON fields, text summary, replay instructions |
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
