# Testing and evaluation plan

**Status:** proposed plan. No results have been measured. Testing must show the product works and determine whether the research hypothesis holds; a polished scripted video is insufficient evidence by itself.

## Verification layers

| Layer | What it proves | Required cases |
| --- | --- | --- |
| Domain unit tests | Contract, comparator, state, and budget rules | threshold ties, dtype changes, NaN/Inf, invalid shape/range, exhausted budget |
| Runner integration | Identical inputs execute in pinned environments and artifacts verify | old/old, new/new, old/new; timeouts; nonzero exit; manifest/hash mismatch |
| Search/reduction | Valid witness is found and remains valid when shrunk | seeded conclusion change; invalid candidate rejected; reduction replayed |
| Intervention | Proposed mechanism is tested, not merely narrated | known mechanism supported; plausible wrong mechanism contradicted |
| Repair gate | Frozen checks cannot be weakened or leaked | overfitted patch rejected; good patch passes; missing oracle inconclusive |
| Security | Untrusted code/model content stays within authority | network attempt, secret read, path traversal, prompt injection, test-weakening patch |
| UI/accessibility | Evidence and uncertainty can be inspected by a person | keyboard journey, screen-reader status, exact numeric values, export |

## Deterministic demonstration fixture

Create a small scientific Python pipeline with a documented NumPy 1.26-to-2.0 behavior difference, a domain-valid input range, a threshold-dependent conclusion, and sparse pre-existing tests that pass in both versions. Verify the difference empirically before claiming it. Seed a few ordinary inputs that do not trigger it; the live search must discover a new valid witness. Record the function and trace point where dtype/precision diverges. Keep this fixture separate from the product code and label it **constructed demonstration case**, not an independently observed research defect.

The fixture must include a known bad patch that only handles the discovered witness, a candidate repair, and precommitted holdouts. The verifier runs both patches against identical frozen checks. A correct-looking patch that changes the contract, tests, dependency version, or runner is rejected before evaluation. If no candidate repair meets the approved contract, show `inconclusive` rather than manufacturing success.

## Comparison policy

Numerical equality is contract-specific. Compare shapes and dtype if the contract requires them; compare finite values using declared absolute/relative tolerances; treat NaN/Inf and signed zero according to explicit policy; compare the declared conclusion separately. A changed float below a threshold need not be a conclusion change. A conclusion change is an observed instability, not automatic proof the new version is wrong. [NumPy testing reference](https://numpy.org/doc/stable/reference/generated/numpy.testing.assert_allclose)

Before cross-version comparison, run repeat controls within each environment. For the first demo, use deterministic workflows and assert repeatability under pinned settings. Later stochastic workflows require distributional protocols, independent confirmation and predeclared stopping rules; they are outside P0. Preserve fresh confirmation cases separate from adaptive discovery.

## Research evaluation

Pre-register a small benchmark with four labeled sets: documented upgrade behavior reproduced in an application; constructed edge cases; unchanged controls; deliberately bad repairs. Each case records input validity, expected conclusion relationship, source provenance, and whether it is historical, documented, or synthetic. Do not mix labels in a single aggregate claim.

At equal execution and model budgets, compare: (A) fixed replay of provided samples, (B) random valid search, (C) a generic coding agent with the same runner and information, (D) ReproDelta. Ablate conclusion guidance, validity checks, interventions, and holdout verification. Report raw counts and denominators, not just percentages. Metrics: confirmed conclusion changes, executions to first witness, invalid candidates, unchanged-control false alarms, reduction size and cost, localization accuracy, wrong-patch rejection, new failures, wall time, token use, and inconclusive rate. Do not infer general superiority from the one demo fixture.

Prior work includes differential numerical tests, floating-point localization, and repair methods; this evaluation tests the proposed combination for a bounded workflow. See [Research and decisions](research-and-decisions.md).

## Release gates

**Gate 1, credible demo:** complete the six live steps in the [PRD](product-requirements.md); repeat them from a clean environment; confirm each artifact digest and final report. **Gate 2, safety:** adversarial runner tests pass before accepting any non-curated code. **Gate 3, submission:** public demo stays available and free for judges; repository includes source, license and setup instructions; video is under three minutes and shows a live NVIDIA/Nebius call as part of the workflow. [Hackathon rules](https://nebiusglobalaihackathon.devpost.com/rules)

On failure, identify the exact stage. A setup failure, inconclusive scientific assessment, and confirmed changed conclusion are different outcomes and must remain separate in both API and UI.
