# Database and evidence storage

**Status:** proposed logical schema. The hackathon build may use SQLite plus local content-addressed files for a single trusted operator; PostgreSQL and object storage are the multi-user target. Do not claim either is deployed until verified.

## Storage principles

The database stores metadata and relationships. Large project snapshots, input arrays, stdout/stderr, traces, patches, and reports live in content-addressed artifact storage keyed by SHA-256; database rows hold digest, size, media type, and storage reference. Never use a filename supplied by the project as an authorization key. Preserve original bytes where reproducibility requires them, and redact or omit secrets before model calls and public export.

An investigation has one immutable baseline snapshot, two resolved environment manifests, one approved contract version, and an execution budget. Search attempts and model proposals are append-only. A holdout set is committed before any patch proposal; the repair proposer cannot read its contents. New approvals or inputs create a new investigation revision rather than rewriting prior evidence.

## Entity map

`project 1—N investigation`; `investigation 1—N contract_version`; `investigation 1—N run`; `investigation 1—N candidate`; `candidate 1—N run`; `investigation 1—N intervention`; `investigation 1—N patch`; `patch 1—N verification`; `investigation 1—N event`; all large payloads reference `artifact`. `environment` and `artifact` may be reused by digest, subject to tenant isolation.

## Core tables

| Table | Key fields and constraints | Purpose |
| --- | --- | --- |
| `projects` | `id`, `owner_id`, `name`, `created_at` | authorization root |
| `artifacts` | `id`, `owner_id`, `sha256`, `byte_size`, `media_type`, `storage_key`, `created_at`; unique `(owner_id, sha256)` | immutable bytes and provenance |
| `environments` | `id`, `owner_id`, `manifest_artifact_id`, `manifest_sha256`, `python_version`, `platform`, `image_digest`; unique `(owner_id, manifest_sha256)` | fully resolved runtime |
| `investigations` | `id`, `project_id`, `source_artifact_id`, `old_environment_id`, `new_environment_id`, `status`, `budget_json`, `active_contract_id`, `created_at` | one comparison and budget |
| `contracts` | `id`, `investigation_id`, `version`, `body_json`, `body_sha256`, `state`, `approved_by`, `approved_at`; unique `(investigation_id, version)` | scientific validity and conclusion rule |
| `candidates` | `id`, `investigation_id`, `input_artifact_id`, `input_sha256`, `origin`, `validity`, `rejection_reason`, `parent_id` | searched and reduced inputs |
| `runs` | `id`, `investigation_id`, `candidate_id`, `environment_id`, `kind`, `attempt`, `status`, `output_artifact_id`, `trace_artifact_id`, `resource_json`, `started_at`, `ended_at` | actual executions |
| `proposals` | `id`, `investigation_id`, `kind`, `model_id`, `prompt_template_version`, `request_digest`, `response_artifact_id`, `validation_status`, `created_at` | Nemotron suggestions, never oracles |
| `interventions` | `id`, `investigation_id`, `candidate_id`, `proposal_id`, `change_artifact_id`, `predicted_effect_json`, `observed_effect_json`, `assessment` | controlled mechanism tests |
| `holdout_sets` | `id`, `investigation_id`, `manifest_artifact_id`, `manifest_sha256`, `sealed_at` | frozen independent cases |
| `patches` | `id`, `investigation_id`, `proposal_id`, `diff_artifact_id`, `diff_sha256`, `created_at` | proposed source edit |
| `verifications` | `id`, `patch_id`, `holdout_set_id`, `status`, `summary_artifact_id`, `resource_json`, `created_at` | independent gate result |
| `events` | `id`, `investigation_id`, `sequence_no`, `actor`, `event_type`, `payload_json`, `occurred_at`; unique `(investigation_id, sequence_no)` | append-only audit trail |

`owner_id` is an application tenant identifier even if the hackathon build has one owner. Public demo access uses a read-only curated investigation or a purpose-built guest tenant. Every query must constrain by owner/project; PostgreSQL row-level security can reinforce this when configured and tested. [PostgreSQL RLS documentation](https://www.postgresql.org/docs/18/ddl-rowsecurity.html)

## Contract document shape

The approved `body_json` has a versioned schema with: `input_schema` (field names, dtype, shape, finite-value rules, ranges, cross-field constraints); `output_selector`; `conclusion` (operator, threshold, units, tie behavior); `comparison` (absolute and relative tolerances, NaN/Inf policy, dtype policy); `resource_limits`; `assumptions`; and `review_provenance`. Treat absent or ambiguous comparison fields as invalid, not permissive defaults. The contract hash covers canonical JSON bytes. The exact JSON Schema should be checked into the implementation repository before accepting user data.

## State and integrity rules

Investigation states: `draft → preflight → approved → running → verifying → completed`, with `failed` and `cancelled` terminal alternatives. A run is `queued → running → succeeded|failed|timed_out|cancelled`; never relabel a failed run as a conclusion change. The active contract may only point to `approved` state. A verifier may only use a holdout sealed before its patch was proposed. Each paired comparison requires identical input digest and a recorded environment digest for each side. Each reduction must link to its parent candidate and pass the same approved validity predicate.

Use transactional inserts for state transitions and event sequence numbers. Artifacts are written to a temporary key, hashed, verified, and then made immutable before the database points to them. Garbage collection may remove unreferenced temporary objects; never silently mutate a referenced artifact. Preserve provenance of official documentation passages separately from model assertions.

## Example migration sketch

This is a schema sketch, not a migration ready to run unchanged:

```sql
CREATE TABLE projects (
  id UUID PRIMARY KEY,
  owner_id UUID NOT NULL,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE investigations (
  id UUID PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects(id),
  status TEXT NOT NULL CHECK (status IN
    ('draft','preflight','approved','running','verifying','completed','failed','cancelled')),
  budget_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX investigations_project_created_idx
  ON investigations(project_id, created_at DESC);
```

Follow [Security](security.md) for retention, deletion, tenant access and sensitive data. Follow [Testing](testing.md) for migration and invariant checks.
