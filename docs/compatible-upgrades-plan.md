# Compatible scientific investigation upgrades

Review baseline: `cafc744` (18 October 2026). Branch: `feat/scientific-investigation-v2`.

## Scope decision

The user deferred the separate isolated project runner. Keep the public demo,
trusted-source allowlist, OAuth permissions, deployment and existing contract
semantics. Do not enable arbitrary repository execution or present fixtures as
independent downstream defects. No deployment, paid model calls or merges are
authorized by this implementation phase.

## Compatibility review before implementation

1. Real-project adapter: **deferred**. Hosted API and workers share one container.
   Keep unsupported repositories rejected. No new source-upload/shell endpoint.
2. Executable contracts: **compatible** as an additive, typed predicate language.
   Preserve the default contract's canonical bytes/hash. Existing nonempty free
   text cross-constraints were not enforced; fail explicitly on unsupported syntax
   rather than inventing its meaning. No eval, Python expressions or imports.
3. Repair checks: **compatible**. Add measured-value preservation on previously
   consistent holdouts using the approved comparison tolerances. Retain the
   compatibility-only verdict and explicitly report that scientific correctness
   needs an independently approved oracle. Never designate the old version as
   ground truth automatically. Missing measurements cannot pass.
4. Attribution/localization: **partially compatible**. Capture bounded named
   checkpoints in the trusted child launcher without editing fixture source.
   Compare common checkpoints and distinguish first observed dtype/value drift
   from a cause. Keep predefined intervention separate. Four source/environment
   combinations require the deferred project runner and are not implemented.
5. Adaptive search/reduction: **compatible** as an opt-in strategy. Preserve sweep
   and random defaults. Use approved shape/bounds and measured distance to the
   conclusion threshold; record seed/strategy/candidates. Revalidate every input,
   use the same budget runner, and do not read holdouts or call a paid model.
   General multi-project/model-driven experiment selection remains deferred.
6. Reproducible regression export: **compatible**. Offer an owned downloadable ZIP
   with exact trusted sources, contract, witness, expected observations, hashes
   and a standalone pytest replay against two explicitly supplied interpreters.
   Treat exports as data: no exec/eval or shell interpolation; refuse incomplete
   and simulated evidence. Keep current JSON/text exports and historical reports.
   GitHub write/check publishing is deferred; no OAuth permission expansion.
7. Independent evaluation: **compatible tooling**, not fabricated validation.
   Add multi-seed, equal-budget runs with raw denominators, provenance labels,
   reproducibility metadata and explicit unknown ground truth. Keep constructed
   cases separate from documented mechanisms. Independently confirmed downstream
   scientific defects require domain-reviewed sources and remain a release gate.

## Execution order and intended files

1. Record baseline tests and default contract hash before editing.
2. Add predicate types/evaluator in `app/domain/constraints.py`; integrate the
   existing validator and add a non-executing contract validation endpoint.
3. Strengthen `patch_verifier.py` with numeric preservation checks and exported
   scope. Tests distinguish agreement, preserved measurements and scientific truth.
4. Instrument `runner/execute_project.py` with bounded trusted checkpoints; attach
   observations through existing worker outputs and evidence/report extensions.
5. Add opt-in adaptive candidate generation to existing search and API strategy
   selection; extend reduction without weakening validity or claiming global
   minimality. Keep deterministic seeds and failure behavior.
6. Add regression bundle builder/replayer and owned endpoint. Bind every source,
   input, contract and observation to digests; test tampering and fresh replay.
7. Add evaluation CLI with several seeds and explicit provenance/status. Do not
   run expensive comparisons in every ordinary supported investigation.
8. Display diagnostics, repair measurement checks, search provenance and export
   eligibility in existing evidence/report pages. Use textContent for external
   content; omit new sections for historical reports lacking fields.

## Output review

The primary result remains the measured old/new ratio and declared conclusion.
New disclosures explain checkpoint differences and measured repair preservation.
No new global safe/unsafe, scientific correctness or causal-proof badge. A
regression ZIP button appears only for eligible pinned-worker reports. Existing
JSON/text/print controls continue working. New sections render in both evidence
and printable views, preserve exact stored values and work with old reports.

## Gates and rollback

- Run Python 3.12 baseline/full tests, frontend tests and Ruff. Use the existing
  actual NumPy 1.26.4/2.0.2 interpreter harness for numerical and patch checks.
- Verify fixture source hash and default contract hash stay unchanged.
- Test unsupported predicates, malformed/oversized inputs, worker failures,
  exhausted budgets, bad repairs, export tampering, ownership and old reports.
- Run the updated investigation through real pinned interpreters and inspect
  the rendered output locally. Docker is absent on this host; rely on measured
  Docker CI and report any unverified deployment gates explicitly.
- New strategy is opt-in; all new output fields are additive. Existing persisted
  records need no migration. Reverting this branch must restore baseline behavior.
- Push a separate branch, open a concise PR against main, await checks, fetch
  latest main and verify mergeability. Do not merge or deploy.

## Internal plan review

Checked against `AGENTS.md`, current worker manifest, frozen contract/evidence,
shared budget ledger, owner dependency, report renderer and deployment topology.
Key risks resolved in design: no new untrusted execution path, unchanged default
hashes, no hidden billable search, no benchmark novelty claim, no new write scope.
Implementation/tests must still establish each gate; this plan is not a guarantee.
