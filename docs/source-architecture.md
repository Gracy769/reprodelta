# Agent Replay Lab: revised research direction

Research review: 10 October 2026. This proposal augments the attached concept document. No project implementation was supplied or assessed, and no experiments reported below have been run. Proposed capabilities and evaluation targets are explicitly distinguished from findings in existing sources.

## Proposed problem statement

Research teams need to know whether an environment change alters a meaningful result even when their existing tests pass. Agent Replay Lab searches for valid inputs that expose a change in a declared scientific conclusion, minimizes the counterexample, tests competing explanations, and evaluates a proposed repair against independent checks.

The initial audience is research software engineers and small scientific Python teams maintaining numerical pipelines with limited regression coverage. Validate this audience with users before assuming commercial demand.

## What changes from the attached concept

The attached document proposes paired execution, intermediate-value comparison, explanations, patches and regression tests. Retain these as infrastructure. Add an explicit scientific contract, active counterexample search, controlled interventions, independent repair checks and bounded verdicts. Integrate with an existing dependency-update PR rather than building package-update scheduling as the main product.

[Dependabot's documented workflow](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependabot-version-updates) maintains dependency versions through update PRs. Your differentiated deliverable is an executable counterexample and evidence about consequences for this particular workflow.

## Prior work that limits the novelty claim

- [FLiT](https://github.com/PRUNERS/FLiT) compares floating-point variability across compilation environments and includes file/function localization. Numerical comparison and bisection are established techniques.
- [FPDiff](https://github.com/ucd-plse/FPDiff) generates differential tests for numerical-library special functions. Automatically finding numerical discrepancies is established.
- [Herbgrind](https://arxiv.org/abs/1705.10416) investigates root causes of floating-point error in low-level programs. Explaining numerical error is established.
- [Herbie](https://herbie.uwplse.org/) rewrites numerical expressions to improve accuracy. Floating-point repair is established.
- [PCREQ](https://github.com/PCART-tools/PCREQ) infers compatible Python requirements using version and code compatibility analysis. Automated upgrade compatibility is established.
- [LLM-assisted metamorphic-testing research](https://portalcientifico.uam.es/en/ipublic/item/10727913) includes relation inference, follow-up test generation and validation/fixing workflows. AI-generated properties alone are not a new research contribution.

The opportunities below are project-specific research hypotheses inferred from these sources. This review does not establish that the combinations are globally unstudied or patentable.

## Gap 1: searching for changes in scientific conclusions

**Question:** Can a contract-guided search expose meaningful conclusion changes more efficiently than fixed examples or random valid inputs?

Comparing every numeric value produces differences whose practical importance is unclear. The user should declare a consequential output, such as a threshold classification, an ordering, a conservation residual, or an accepted error bound. The contract is specific to the workflow and records valid input ranges, assumptions and acceptable margins.

The agent proposes tests that target this output. The execution engine determines whether a contract was violated or a conclusion changed. A conclusion difference is a stability finding, not automatically a bug; the old conclusion may be wrong.

**Initial implementation:** One deterministic NumPy workflow with a threshold-dependent output. Inputs must remain within the user-approved domain. Search near sensitive regions while retaining broader valid samples for verification.

**Evaluation:** Compare against fixed replay and random search under the same execution budget. Measure independently confirmed conclusion-changing cases found, executions to discovery, invalid inputs rejected and unchanged controls falsely flagged.

[NumPy's migration guide](https://numpy.org/doc/2.0/numpy_2_0_migration_guide.html) documents scalar-promotion changes that can alter precision or overflow behaviour. This supports the failure mechanism; it does not establish that a particular research conclusion changed.

## Gap 2: reducing a failure to a valid, understandable witness

**Question:** Can guided reduction preserve the conclusion-changing failure and scientific input constraints while reducing the data and code required to reproduce it?

A researcher should receive a small executable example, not thousands of differing array elements. Reduce rows, columns and pipeline stages while retaining the observed failure and the declared preconditions. Record the smallest witness found under the budget; do not promise a globally minimal example.

**Initial implementation:** Array/data subset reduction plus a restricted set of transformations. Re-run both environments after every accepted reduction. Preserve dtype, shape constraints and any domain conditions needed by the property.

**Evaluation:** Compare with unguided deletion. Measure witness size, reduction cost, faithful reproduction and constraint preservation. A counterexample outside the accepted domain cannot count as a successful result.

This is an application-level extension built on established differential testing, exemplified by [FPDiff](https://github.com/ucd-plse/FPDiff), rather than a claim to invent test generation or reduction.

## Gap 3: testing causality rather than explaining a trace

**Question:** Can targeted interventions identify the mechanism responsible for a conclusion change, rather than merely the earliest observed numeric difference?

Preserve input hashes, Python version, platform, thread settings and non-target dependencies where compatible. Compare actual resolved environments. If several packages necessarily change, record that confound and test compatible combinations instead of attributing everything to NumPy.

The model proposes an explanation and a testable intervention. For example, make precision explicit at one suspected operation. Re-run and observe whether the downstream conclusion difference disappears. Report this as evidence supporting a mechanism within tested cases, not universal causal proof.

**Initial implementation:** Trace selected Python-level boundaries and numerical calls, recording dtype, shape and selected values. Native library internals are outside the initial instrumentation scope. Instrumentation itself needs a control showing that it preserves the observed result.

**Evaluation:** Use cases with known mechanisms. Measure localization accuracy, interventions required, unsupported explanations and reproduction of the downstream effect. Compare against earliest-difference localization and an LLM explanation without interventions.

[Herbgrind](https://arxiv.org/abs/1705.10416) already localizes numerical error; [causal testing research](https://arxiv.org/abs/2209.00357) already studies expected effects in scientific models. The proposed distinction is their application to controlled Python upgrade investigations with a small executable evidence bundle.

## Gap 4: validating generated scientific properties

**Question:** Can executable preconditions and independent references reduce false alarms from LLM-proposed metamorphic properties?

A metamorphic property describes how outputs should relate when inputs are transformed. It must be conditional. Scaling both the response and predictions by a positive constant scales mean-squared error by that constant squared, assuming the same observations, weights and definition. This is a valid narrow template; a claim that arbitrary models should always scale linearly is not.

For every property, record its provenance, applicability conditions, accepted domain and tolerance. Candidate properties are proposals until independently justified or reviewed. Running successfully on the old version is supporting evidence only: a bad property can pass both versions.

**Initial implementation:** A small reviewed template library, analytic examples and known violating mutations. Require the reviewer to approve assumptions before using a generated property to label a numerical regression.

**Evaluation:** Measure expert-confirmed validity, false-alarm rate, known-fault detection and rejection of invalid-domain cases. Do not use the proposing model's own confidence as the correctness oracle.

[Bug or not Bug?](https://arxiv.org/abs/2305.09640) directly discusses violations caused by properties not fitting particular inputs. [Recent LLM-based workflows](https://portalcientifico.uam.es/en/ipublic/item/10727913) show that generation itself is prior work. The research target is calibrated applicability to scientific upgrade cases.

## Gap 5: distinguishing upgrade effects from ordinary run variation

**Question:** Can within-environment controls and fresh confirmation runs reduce false regression reports under a fixed compute budget?

Compare old-versus-old and new-versus-new variation as well as old-versus-new. Record seeds and relevant runtime configuration. For stochastic workflows, decide whether the contract concerns individual outcomes or distributions; they require different checks.

Adaptive search can cherry-pick unusually large differences. Keep fresh confirmation cases separate from discovery. A nonsignificant difference does not establish equivalence; the report should remain inconclusive when the specified margin cannot be assessed with the available runs.

**Initial implementation:** Deterministic examples first, with a simple repeatability check. Add stochastic and GPU workflows later; they are not required for the first demo.

**Evaluation:** False alarms on unchanged configurations, sensitivity to known shifts, run costs and inconclusive verdict frequency. Statistical procedures and stopping rules must be specified before claiming calibrated confidence.

[PyTorch's numerical-accuracy documentation](https://docs.pytorch.org/docs/2.14/notes/numerical_accuracy.html) warns against expecting bitwise identity across releases and platforms. This motivates careful comparison, not a claim that PyTorch itself is missing numerical diagnostics.

## Gap 6: evaluating a repair beyond the discovering example

**Question:** Can precommitted holdout inputs, independent properties and resource constraints reject overfitted numerical patches?

Freeze verification checks before patch generation. Give the repair agent the counterexample and approved contracts, but not permission to weaken them. Evaluate the patch on unseen valid inputs, boundary cases, applicable independent properties and an analytic/high-precision reference where appropriate.

Higher precision is not a universal oracle and a blanket float64 conversion is not automatically acceptable. The contract also states any necessary runtime, memory and output-dtype limits. An unavailable or unsupported oracle requires an inconclusive result.

**Initial implementation:** Restrict patch transformations, cap attempts, and compare patched results in both environments. A version rollback is a mitigation, not evidence that the upgraded workflow was repaired.

**Evaluation:** Independent patch acceptance, wrong-patch rejection, new failures, time/memory overhead and exhausted-budget outcomes. Compare with a generic coding agent given the same examples, information and budget.

[Patch-overfitting research](https://discovery.ucl.ac.uk/id/eprint/10192372/) documents patches that pass available tests but remain incorrect. [Herbie](https://herbie.uwplse.org/) supplies relevant numerical-rewriting prior work. Your contribution would be evidence about application-level repairs after dependency changes, not a proof that arbitrary patches are correct.

## Revised architecture

1. **Project intake:** Project snapshot, old/new resolved environments, entry point, sample inputs, approved contracts and execution budget.
2. **Paired runner:** Isolated Nebius jobs run the same immutable inputs; record environment and input hashes, outputs, selected trace events and resource use.
3. **Contract proposer:** Nemotron suggests conditional properties from code and permitted documentation. The reviewed contract becomes immutable for this investigation.
4. **Search and reduction controller:** Selects valid inputs, runs comparisons, prioritizes consequential changes and shrinks witnesses while preserving preconditions.
5. **Intervention controller:** Proposes and runs targeted experiments; retains supported and contradicted explanations.
6. **Repair proposer:** Nemotron proposes bounded edits supported by the witness and intervention results.
7. **Independent verifier:** Runs frozen holdouts and resource checks without allowing the repair loop to alter acceptance criteria.
8. **Evidence report:** Includes replay command, versions, hashes, witness, tested assumptions, intervention results, patch diff, coverage and limitations.

Verdicts should distinguish: observed drift; supported conclusion change; pre-existing contract violation; repair passes stated checks; no violation found within tested scope; inconclusive; and setup failure. Avoid a universal SAFE/UNSAFE badge.

## Hackathon scope and demonstration

Implement Python and NumPy 1.26 to 2.0 only. Choose a supported numerical operation and one meaningful output contract. Build gaps 1–3 and a small independent verification gate from gap 6. Treat property-template review as essential validation; defer full stochastic/GPU support.

Proposed demonstration, not a measured result:

1. Both versions execute successfully and existing checks pass.
2. The search discovers a valid input for which a declared conclusion changes.
3. The system reduces that input and displays the relevant precision difference.
4. A targeted intervention supports the suspected mechanism.
5. An intentionally overfitted patch is rejected by frozen verification checks.
6. A better patch passes the stated checks, or the system explains why the result remains inconclusive.

This is stronger than a prewritten changelog explanation only if steps 2–6 actually execute.

## Evaluation asset worth publishing

Create a small benchmark of successful executions with hidden numerical or conclusion differences. Include independently validated upgrade cases, unchanged controls, constructed edge cases and deliberately bad repairs. Label documented behaviour, historical defects and synthetic examples separately.

[BUMP](https://github.com/chains-project/bump) focuses on Java dependency updates where builds/checks fail. It is useful methodological prior work, but not direct ground truth for successful scientific executions with changed conclusions. This review does not establish that no other suitable benchmark exists.

Compare fixed replay, random valid search, a generic coding agent with the same runner, and the proposed system. Ablate conclusion guidance, validity checks, interventions and holdout verification. Match budgets and available information. Report detection, false alarms, localization, patch rejection and compute cost rather than a single unexplained success rate.

## Nebius, NVIDIA and Tavily roles

- NVIDIA Nemotron on Nebius Token Factory proposes contracts, next experiments, explanations and patches. Executable checks establish observations; model narration does not establish truth.
- Nebius Serverless Jobs run paired environments, interventions and verification workloads. The required platform/model use belongs inside the working investigation loop.
- Tavily can retrieve version-specific official documentation or issue evidence at runtime. Cache retrieved passages and provenance in the evidence report. Documentation is a hypothesis source, not an experimental oracle.

[The hackathon rules](https://nebiusglobalaihackathon.devpost.com/rules) require a Nebius runtime and an NVIDIA open model, and tie Tavily bonus eligibility to a functional runtime API call. Prize eligibility is not a reason to add irrelevant search calls.

## Build decision

Proceed if the team can reliably run paired environments, express one reviewed contract, discover a valid witness and independently check a repair. The core research hypothesis is whether contract-guided search finds consequential regressions more effectively at the same budget. Scheduling updates, adding more agent personas and summarizing release notes should remain supporting features.
