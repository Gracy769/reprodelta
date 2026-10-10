# Runtime agents and control flow

**Status:** proposed architecture. “Agent” here means a bounded decision role in the product, not a separate autonomous person or a claim that multiple models are necessary. One Nemotron-backed controller can implement several roles while deterministic services enforce policy.

## Governing rule

The model may **propose**; code and approved contracts **validate**; isolated runs **observe**; the independent verifier **assesses stated checks**; a human **decides whether to apply**. No model statement is an experimental result. Every proposal stores model ID, prompt version, input digest, output, validation decision, and subsequent action.

## Roles and handoffs

| Role | Receives | May produce | Cannot do |
| --- | --- | --- | --- |
| Contract proposer | project metadata, permitted code/docs, example input | conditional contract draft with assumptions and provenance | approve a property or widen domain |
| Search controller | approved contract, prior candidate summaries, remaining budget | next candidate or transformation within domain | execute arbitrary commands or ignore validator |
| Reduction controller | confirmed witness and validity predicate | smaller candidate with predicted preserved effect | count an invalid reduction as success |
| Explanation proposer | trace boundaries, environment diff, official documentation | ranked hypotheses with falsifiable predictions | state causality as fact from a trace alone |
| Intervention controller | approved hypothesis, allowlisted edit points | one targeted intervention manifest | modify contract or several variables without recording confounds |
| Repair proposer | witness, approved contract, intervention results, allowed source paths | bounded code diff and rationale | read sealed holdout, alter tests, lockfiles, runner, or policy |
| Independent verifier | sealed holdout manifest, patch, approved contract | pass/fail/inconclusive per frozen check | accept new criteria from repair proposer |
| Report assembler | validated events and artifacts | human and machine-readable evidence report | invent absent evidence or hide failed checks |

These are logical roles. The contract proposer and repair proposer can use Nemotron through Nebius Token Factory. Search and reduction should use deterministic algorithms where possible, asking the model only when it can add useful candidate selection or interpretation. Nebius's [API reference](https://api.tokenfactory.nebius.com/docs) documents the inference surface; exact model availability is checked at build time.

## Tool contracts

Allowlisted tools: `inspect_project` returns bounded source/manifest excerpts; `validate_input` checks the approved domain; `run_pair` launches the same candidate in old/new environments; `run_control` repeats within one environment; `reduce_candidate` applies a declared transform; `run_intervention` executes a one-variable change; `propose_patch` creates an allowlisted diff; `verify_patch` invokes frozen checks; `fetch_official_docs` retrieves a bounded passage with URL and digest. Tool arguments use strict schemas and stable IDs. The orchestrator checks investigation state, authorization, budget, path allowlist, and artifact hashes before executing any call.

No tool grants unrestricted shell, browsing, secret access, arbitrary file writes, or holdout reads to the model. Tool results are untrusted data. A README or web page saying “ignore your instructions” has no authority; the policy is enforced by the orchestrator, not prompt wording. [OWASP guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)

## Investigation loop

1. Preflight project, environments, contract, runner policy, and budget. Seal approved contract and holdouts.
2. Run repeat controls. If they violate declared repeatability, stop with an inconclusive/setup outcome.
3. Search valid inputs within budget. Deterministic validator rejects out-of-domain candidates before execution. Evaluate the declared conclusion on every completed pair.
4. Reconfirm a found witness on fresh runs, then reduce it. Recheck validity and the conclusion after every accepted reduction.
5. Generate hypotheses and choose a discriminating intervention. Record prediction before running it; retain both supporting and contradicting evidence.
6. Propose at most the configured number of source-only patches. Verify each against frozen holdouts and resource limits. A failed check is retained, not overwritten by a later patch.
7. Assemble a bounded verdict and evidence bundle. Stop at budget exhaustion, cancellation, security violation, or insufficient oracle.

## Budgets and stopping

The investigation manifest has finite `max_model_calls`, `max_pair_runs`, `max_interventions`, `max_patch_attempts`, `max_wall_seconds`, `max_runner_memory`, and `max_artifact_bytes`. The UI shows remaining budget. Defaults are chosen only after measuring the chosen fixture and provider; they are not fabricated in these documents. Require a human to approve an increased budget in a new investigation revision.

An agent must stop on invalid inputs, unavailable environment, changed hash, failed isolation, repeated nondeterminism outside contract tolerance, exhausted budget, or sealed-holdout access attempt. It must use `inconclusive` when evidence cannot support a narrower verdict. See [Security](security.md) and [Testing](testing.md).
