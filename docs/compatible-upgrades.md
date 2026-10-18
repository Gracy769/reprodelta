# Compatible scientific workflow extensions

These extensions retain the curated demo and its approved contract. The separate
project runner was deferred by the user. Unsupported connected repositories still
cannot execute. No new deployment service, OAuth write permission or model charge
is needed. The reviewed implementation plan is `compatible-upgrades-plan.md`.

## Executable input relationships

`InputSchema.cross_constraints` accepts typed objects with `kind`, `fields`,
`target` and `atol`. Supported kinds: `sum_close`, `same_shape`, `symmetric`,
`positive_definite`, `nondecreasing`. Examples:

```json
{"kind":"sum_close","fields":["probabilities"],"target":1.0,"atol":0.000001}
```

`POST /api/contracts/validate` accepts `{"contract": CONTRACT_BODY,
"input_json": SAMPLE}` and returns validity, reason, hash and an explicit
validation-only scope. It does not approve the contract or authorize execution.
The generic scientific output/multi-project executor remains deferred. Free-text
constraints used to be ignored; unsupported strings now fail explicitly. JSON
strings containing a supported predicate can still be decoded. No eval or Python
expression language is supported. Predicates accept at most 4096 finite values;
positive-definite matrices are limited to 64 by 64. Default contract bytes/hash
are unchanged because its cross-constraints remain null.

## Repair measurement preservation

The executed model-diff gate now preserves numeric ratios on previously
conclusion-consistent holdouts, separately for each environment, using the
approved `atol + rtol * abs(baseline)` rule. A patch that returns a materially
different ratio with the same classification fails. Missing measurements never
pass. Witness/holdout conclusions, source hashes, versions and dtype policy are
still checked, and patches cannot alter tests or verification criteria.

`RepairOracle` is an optional library-level independent reference map, keyed by
input hash. The verifier copies/hashes it before executing and requires complete
reference coverage. It is not an approval workflow: callers must arrange domain
review and freeze it before requesting a model patch. The hosted demo supplies no
independent scientific oracle and says so. Neither old nor new NumPy is assumed
scientifically correct; repair verdicts remain compatibility checks.

## Checkpoints and attribution boundary

The trusted child launcher records the first available `normalized`, `power`,
`high_power`, `total_power` and `ratio` locals in `compute_power_ratio`. It adds
exact values, shapes, dtypes and a launcher source digest to outputs. It does not
edit fixture source or instrument arbitrary uploaded code. The report compares
the common named checkpoints and identifies the first observed exact difference.
This is localization, not causal proof; the predefined intervention is separate.
Synthetic/local simulation has no native traces. Mixed source/environment
attribution and general tracing require the deferred project runner.

## Adaptive search and numeric simplification

The demo selector and investigation creation API accept `search_strategy` values
`sweep` (unchanged default), `random`, and opt-in `adaptive`. Adaptive version 1
uses seeded random exploration, measured threshold distance, interpolation
between measured opposite sides of the boundary, bounded coordinate mutations
and adjacent representable values. Interpolation is a tested candidate, never an
assumption of monotonicity or validity. It does not
embed a known witness, read holdouts or call a model. It supports bounded 1D
float32/float64 fields; input validation and the shared ledger remain authoritative.
A search can legitimately exhaust its budget without finding a witness.

Adaptive witnesses additionally attempt numeric simplification after the existing
neutral pass. Every accepted transform rechecks validity and the paired outcome;
the complexity measure is compact JSON representation length, not mathematical
minimality. Existing sweep reduction is unchanged. Reports retain seed, strategy,
candidate inputs, hashes, outcomes, rejections and stop reason.

## Portable recorded-regression test

Eligible new reports expose `GET /api/investigations/{id}/regression.zip` under
the same ownership checks as evidence. The archive includes the exact trusted
sources, contract, input, recorded observations, integrity manifest and standalone
pytest test. Both successful witness source attestations must match the snapshot;
NumPy versions must be 1.26.4/2.0.2. Simulated, historical/unattested, incomplete or
tampered evidence returns 409 instead of manufacturing a replay.

After extracting a bundle from a trusted origin, set `REPRODELTA_OLD_PYTHON` and
`REPRODELTA_NEW_PYTHON` to Python 3.12 interpreters with the respective NumPy pins.
Run `python -m pytest test_regression.py -q`. Replay checks each included artifact,
then asserts exact recorded ratios, dtypes, runtime versions and declared outcomes.
It preserves the *observed difference*, not a repaired or scientifically correct
result. On drift, the test fails. Child environments exclude service credentials.
No dependency installation or shell interpolation occurs. The download response's
`X-Content-SHA256` binds the ZIP for external integrity checks; hashes are not
signatures and do not authenticate an attacker-replaced archive.

GitHub check publishing is not enabled. Maintainers can run the exported pytest
test in their existing CI without granting ReproDelta write access. Existing
JSON/text and printable reports remain available. New fields are additive under
report schema 3 with `scientific_extensions_version: "1"`; historical renderers
can ignore them. Historical benchmark replay accepts added trace metadata while
still checking every previously recorded output key and runtime version.

## Multi-seed evaluation

```powershell
python scripts/evaluate_searches.py --old-python OLD_PYTHON --new-python NEW_PYTHON --seeds 13 42 97 --pairs 4 --max-runs 1000 --output evaluation.json
```

Use `--simulate` explicitly for a development-only simulation; this is labelled.
No live Nebius calls occur. The CLI records per-seed raw rows, planned/attempted
seed denominators, execution counts, first-witness counts, negative-control false
alarms, protocol hash and SHA-256 sidecar. A shared finite ledger bounds the whole
evaluation. Incomplete evaluations exit nonzero and retain partial observations.
Existing strategies have equal caps per case/seed. Adaptive is evaluated only on
power ratio with the same cap and stops at first witness; this limitation is
exported. Do not interpret a conditional mean over successes as overall success.

Power and sensor remain constructed cases; least-squares uses an independently
documented NumPy default change with a constructed matrix. No new downstream
scientific defect or scientific ground truth is claimed. Independent external
cases and domain review are still required before asserting general superiority.

The committed `docs/evidence/compatible-search-evaluation.json` records 104 actual
native executions (seeds 13 and 42, two-pair caps) with a SHA-256 sidecar. On power
ratio, informed sweep found a witness for both seeds; adaptive, random and fixed
replay found none at this small cap. This is evidence of the limitation, not a
claim that the new adaptive strategy improves discovery. Its larger-budget
effectiveness remains to be measured on independent cases.

## Output and verification

The primary before/after result remains unchanged. Evidence and printable pages
add expandable measurement-preservation, checkpoint and candidate records. All
source-derived strings use textContent. Native-only ZIP export is hidden for
historical/unattested reports. API and frontend tests cover those boundaries.
No database migration is needed; existing storage retains additive JSON fields.
The deployment still supports one API process. Docker/live rollout and paid
provider calls remain distinct operator/CI gates, never inferred from unit tests.
