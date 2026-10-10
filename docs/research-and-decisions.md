# Research, prior work, and decisions

**Review date:** 10 October 2026. This document distinguishes checked source facts from project choices and untested hypotheses. The supplied revised architecture is the primary design input; no implementation or benchmark was provided.

## Findings from sources

| Topic | Checked finding | Consequence for this design |
| --- | --- | --- |
| NumPy upgrade mechanism | NumPy 2.0 changed scalar promotion; the migration guide gives examples where precision or overflow can differ. [NumPy](https://numpy.org/doc/2.0/numpy_2_0_migration_guide.html) | Build a fixture around an empirically verified behavior; do not infer a specific scientific conclusion from release notes alone. |
| Dependency updates | Dependabot and Renovate raise update PRs. [GitHub](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependabot-version-updates), [Renovate](https://docs.renovatebot.com/getting-started/use-cases/) | Integrate with an update PR later; package scheduling is not the product's core. |
| Existing regression tools | `pytest-regressions` stores expected numeric/array results; DVC compares experiment metrics and plots. [pytest-regressions](https://github.com/ESSS/pytest-regressions), [DVC](https://doc.dvc.org/user-guide/experiment-management/comparing-experiments) | Numerical comparison alone is not a novelty claim. |
| Differential testing and localization | FLiT detects floating-point variability across compilation environments and localizes file/function differences; FPDiff searches numerical-library discrepancies. [FLiT](https://github.com/PRUNERS/FLiT), [FPDiff](https://github.com/ucd-plse/FPDiff) | Search and localization are established methods; test the application-level combination against baselines. |
| Numerical repair | Herbie improves accuracy of floating-point expressions. [Herbie](https://herbie.uwplse.org/) | Do not claim to invent floating-point rewriting; assess whether a repair preserves the declared application contract. |
| Upgrade compatibility | PCREQ infers compatible Python requirements. [PCREQ](https://github.com/PCART-tools/PCREQ) | Distinguish runtime-successful conclusion drift from API/version compatibility work. |
| Metamorphic testing | Prior research examines cases where a proposed relation does not apply to an input. [Bug or not Bug?](https://arxiv.org/abs/2305.09640) | Require explicit preconditions and human review before a property can label a violation. |
| Benchmark precedent | BUMP is a reproducible dataset of breaking dependency updates. [BUMP](https://github.com/chains-project/bump) | Useful benchmark method, but not direct ground truth for successful scientific runs with changed conclusions. |
| Hackathon rule | A working app must use an NVIDIA open source model and run on Nebius Token Factory or AI Cloud; the coding track is appropriate. [Rules](https://nebiusglobalaihackathon.devpost.com/rules), [FAQ](https://nebiusglobalaihackathon.devpost.com/details/faqs) | Put a live Nemotron call in the investigation loop. Jobs are an architectural choice, not a rule requirement. |
| Security and accessibility | OWASP places tool authority outside untrusted model content; W3C requires visible focus and perceivable status updates. [OWASP](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html), [W3C focus](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible), [W3C status](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html) | Keep model proposals separate from execution facts; build an accessible evidence UI. |

## Decisions for the first build

**D1 — Contract before search.** The investigator approves valid input domain and conclusion semantics before search. This prevents declaring every floating-point difference meaningful after seeing the result. Trade-off: onboarding takes longer.

**D2 — Curated deterministic fixture.** Use a single reproducible NumPy upgrade case first. Trade-off: a convincing demo does not yet establish generality. Evaluate on additional cases before wider claims.

**D3 — Immutable evidence.** Hash source, inputs, environments, outputs, patches, and reports. Freeze holdouts before patch generation. Trade-off: more storage and careful lifecycle management.

**D4 — Model proposals, deterministic authority.** Nemotron suggests contracts, experiments and patches; validators and executions establish what happened. Trade-off: the agent has less apparent autonomy but its claims are inspectable.

**D5 — Scoped verdicts.** Separate observed drift, conclusion change, repair checks, no finding under budget, inconclusive, and setup failure. Trade-off: less simple marketing language, more accurate product behavior.

**D6 — Provider abstraction.** Use Nebius Token Factory for the required NVIDIA model call. Treat Nebius Serverless Jobs as a preferred paired runner only after checking current account access, quotas, network isolation, and costs. The official [Token Factory API](https://api.tokenfactory.nebius.com/docs) is the integration reference.

## Hypotheses to test, not facts

1. Contract-guided search finds valid conclusion changes in fewer executions than fixed or random valid input selection.
2. Guided reduction yields smaller witnesses without losing scientific validity more often than unguided deletion.
3. Targeted interventions identify mechanisms more accurately than earliest-difference narration alone.
4. Frozen holdouts reject overfitted numerical patches that pass a discovered witness.
5. Research software engineers regard this evidence bundle as useful enough to adopt in their dependency-update reviews.

The [Testing plan](testing.md) defines comparable budgets and measurements. Do not present any of these as achieved without a recorded experiment.

## Open decisions and validation work

- Choose a real user-reviewed conclusion contract and threshold; obtain domain justification for its valid input range and units.
- Verify the exact NumPy 1.26/2.0 fixture in pinned images, including all transitive packages. If the mechanism does not reproduce, change the fixture rather than the claim.
- Confirm Nebius model identifier, current Token Factory API behavior, account access, and whether Serverless Jobs can meet runner isolation requirements. The published hackathon FAQ says a Token Factory runtime call can satisfy the Nebius platform requirement.
- Determine the licensed source of any external dataset. Use synthetic demonstration data until permission and privacy are clear.
- Define concrete per-run budgets from a measured prototype; values are deliberately unset in the PRD.
- Interview at least several target maintainers about how they decide whether numerical output changes are acceptable. Treat these interviews as product validation, separate from technical evaluation.

## Claim discipline

Do not call the method globally novel, patentable, causally proven, statistically equivalent, or generally safe based on this review. Documented prior work covers key components. A supported contribution would be a tested combination for successful scientific executions after dependency upgrades, with a benchmark that distinguishes changed conclusions from harmless numeric drift.
