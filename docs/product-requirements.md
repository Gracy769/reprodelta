# Product requirements

**Status:** proposed. **Owner:** hackathon team. **Source:** supplied revised architecture, reviewed 10 October 2026.

## Problem and user

A research software engineer receives a dependency update PR. The workflow starts, runs, and may pass a sparse test suite, but the numerical result or a decision derived from it can change. The engineer needs an executable counterexample and a defensible account of what changed before accepting the upgrade. NumPy documents a concrete mechanism: NumPy 2.0 changed scalar promotion and can change precision or overflow behavior. That documentation does not prove a defect in any particular project. [Source](https://numpy.org/doc/2.0/numpy_2_0_migration_guide.html)

Primary user: a small scientific Python team maintaining a deterministic numerical pipeline. Secondary user: a maintainer reviewing the resulting upgrade PR. Validate these users through interviews or observed workflows before making market claims.

## Product promise and boundaries

Given a runnable project, two resolved environments, approved inputs, and a reviewed scientific contract, the lab will seek a valid conclusion-changing input, reduce it, test one or more explanations with interventions, and evaluate a bounded repair. It reports only what its executions support.

For the hackathon, support Python and one curated NumPy 1.26-to-2.0 upgrade. Do not promise arbitrary repositories, notebooks, stochastic/GPU pipelines, general package migration, or proof that a patch is mathematically correct. An unchanged result under a finite search budget means **no violation found within tested scope**, not “safe.”

## Core user journey

1. Select the curated project or provide an approved snapshot. Inspect the exact old and new lockfiles, Python version, entry point, and sample input.
2. Review a proposed contract: input ranges and structural preconditions; output of interest; decision rule; numerical tolerance; performance and dtype constraints. The user approves or edits it before search.
3. Start a budgeted investigation. The system runs old/old, new/new, and old/new controls, then searches valid inputs. It displays attempted, invalid, and completed executions separately.
4. Inspect a witness. Show input, output values, conclusion on each version, first traced boundary difference, resolved environment differences, and reproducible replay command.
5. Inspect a reduced witness and an intervention. Show which constraints remained true and whether the downstream conclusion changed after the targeted intervention.
6. Review a proposed patch. The frozen verifier runs holdouts and resource checks. The user sees the patch diff and a bounded verdict; only the user applies it to their code.
7. Export a portable evidence report containing hashes, versions, commands, provenance, observations, limitations, and any unresolved confounds.

## Functional requirements

| ID | Requirement | Hackathon acceptance evidence |
| --- | --- | --- |
| F1 | Record immutable project, environment, and input digests before execution. | Report lists matching hashes for replayed runs. |
| F2 | Reject inputs outside an approved contract before running code. | Invalid sample is rejected and cannot count as a witness. |
| F3 | Run both versions on the same input with bounded time, memory, and execution count. | Paired run shows both environment manifests and resource usage. |
| F4 | Establish within-version repeatability before claiming cross-version drift. | An unchanged control yields no false conclusion-change verdict. |
| F5 | Search for an input whose **declared conclusion** changes, not merely any differing float. | Live search finds a valid witness absent from the initial sample set. |
| F6 | Reduce the witness while preserving contract preconditions and the observed change. | Smaller input replays in both environments. |
| F7 | Record a model-proposed, testable explanation and execute a targeted intervention. | Report distinguishes supported, contradicted, and untested explanations. |
| F8 | Propose a bounded patch without changing the contract or frozen holdouts. | Deliberately overfitted patch is rejected; accepted patch passes stated checks, or result is inconclusive. |
| F9 | Export a self-contained evidence bundle. | Another team member can follow the replay steps and inspect limitations. |
| F10 | Make an NVIDIA Nemotron runtime call through Nebius Token Factory for contract/experiment/repair proposals. | Demo and logs identify model, request purpose, and resulting validated action. |

## Verdict vocabulary

Use exactly these user-facing classes, with supporting evidence and scope: `observed drift`, `supported conclusion change`, `pre-existing contract violation`, `repair passes stated checks`, `no violation found within tested scope`, `inconclusive`, `setup failure`. Multiple observations may coexist. Never collapse them to a global green/red “safe” badge.

## Nonfunctional requirements

- Reproducibility: pin package versions, Python image digest, input hash, seeds, locale, thread settings, and relevant environment variables. Report all resolved package changes.
- Isolation: never execute submitted project code in the API or web process. Enforce the security controls in [Security](security.md).
- Auditability: preserve model proposals, tool calls, rejected candidates, intervention results, and verifier outcomes as append-only events.
- Responsiveness: show progress and cancellation; keep the UI usable during long jobs. Do not invent a latency SLA before measuring the chosen runner.
- Accessibility: target WCAG 2.2 AA for the core investigation path and support keyboard operation and screen-reader status updates. See [Design system](design-system.md).
- Cost: define and display per-investigation caps for model calls, paired executions, wall time, and storage before starting.

## Delivery sequence

**P0 — working demonstration:** curated project; approved contract; paired runner; live valid-input search; witness reduction; one intervention; frozen holdouts; evidence report; Nemotron on Nebius. **P1:** authenticated project upload, PR integration, additional templates, richer trace instrumentation. **Later:** stochastic/GPU workflows, statistical equivalence claims, multi-library upgrade attribution, automated PR authoring.

**Implementation note, 11 October 2026:** Account pages and a source-picker
workspace now exist. Managed Neon Auth provides email/password and Google;
GitHub social sign-in requires separate OAuth configuration; this managed
Auth instance does not expose GitLab sign-in. Public home navigation shows
investigations only after a session is confirmed. Users can
connect a source host with a limited read token, select an open PR/MR, and run
a **metadata-only diff review**. That review is not an executable project
investigation and must never be presented as a test result. Arbitrary source
execution remains outside the supported public demo until the isolation,
snapshot, manifest, contract, and ownership checks in the security model are
implemented and verified.

The hackathon submission additionally needs a public repository with an open-source license, working demo, and a public video under three minutes. Check the [official rules](https://nebiusglobalaihackathon.devpost.com/rules) before submission.

**Workspace UX update, 14 October 2026:** The public home page opens with the
curated replay and offers a separate repository diff review. The signed-in
workspace takes a PR/MR URL directly, offers a bounded recent-change list, and
keeps repository search and source settings secondary. Selecting an open change
immediately produces a read-only, file-backed review. The result states its
file limit and that no repository code was executed. The UI must not imply that
this review reproduced a dependency regression.

## Success measures and research test

The research hypothesis is that contract-guided search finds independently confirmed conclusion changes more efficiently than fixed replay or random valid search at an equal execution budget. Track executions to first valid witness, true conclusion changes found, invalid-input rejection, unchanged-control false alarms, reduction size and cost, mechanism localization, wrong-patch rejection, and compute/model cost. The [Testing plan](testing.md) defines comparisons and labels. No improvement is claimed until measured.

## Dependencies and open questions

The team must verify Nebius account access, Job availability and isolation properties, exact model identifier, and permitted input sizes. A domain expert must approve the demonstration contract and the meaning of its threshold. Decide whether public judges receive only a curated sandboxed project; do not accept arbitrary public code until the security gate passes.
