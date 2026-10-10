# Agent Replay Lab — planning documents

**Status: proposed design, 10 October 2026.** No product implementation or experimental result was supplied. The documents below are specifications for a hackathon build, not claims that these features already work.

Agent Replay Lab investigates a narrow failure: a scientific Python workflow still executes after a dependency upgrade, yet a conclusion the researcher cares about changes. It replays immutable inputs in two environments, searches for a valid counterexample, tests a suspected mechanism, and checks a proposed repair against frozen independent cases.

## Start here

| Document | Purpose |
| --- | --- |
| [Product requirements](docs/product-requirements.md) | Users, scope, workflows, acceptance criteria, and delivery gates |
| [Design system](docs/design-system.md) | Information architecture, interaction, visual tokens, and accessibility |
| [Database](docs/database.md) | Data model, artifact storage, invariants, and draft SQL |
| [Security](docs/security.md) | Threat model and controls for executing uploaded code and using models |
| [Code style](docs/code-style.md) | Implementation conventions and module boundaries |
| [Testing](docs/testing.md) | Test strategy, evaluation plan, and release gates |
| [Agents](docs/agents.md) | Runtime agent responsibilities, tool contracts, and stopping rules |
| [Research and decisions](docs/research-and-decisions.md) | Sources, prior work, assumptions, and open decisions |
| [Source architecture](docs/source-architecture.md) | Unmodified copy of the supplied research direction |

The [source architecture](docs/source-architecture.md) is an unmodified copy of the supplied `D:\Movie\agent-replay-lab-revised-architecture.md`. Its contents are treated as design input. The linked research and provider documentation were checked while writing these specifications.

## Build target

The hackathon version supports one curated deterministic Python project, a NumPy 1.26-to-2.0 comparison, one user-approved conclusion contract, bounded search and reduction, one intervention, and a frozen repair check. It uses an NVIDIA Nemotron model through Nebius Token Factory inside the investigation loop. Nebius Serverless Jobs are a proposed runner; confirm provisioning and isolation properties before relying on them. A local isolated runner may serve development, while a live Nebius model call satisfies the published platform rule. See the [official rules](https://nebiusglobalaihackathon.devpost.com/rules) and [FAQ](https://nebiusglobalaihackathon.devpost.com/details/faqs).

## Shared vocabulary

- **Contract:** approved valid input domain, consequential output, comparison semantics, margin, and resource limits.
- **Witness:** a valid input that reproducibly changes the declared conclusion across environments.
- **Intervention:** one controlled change made to test a proposed mechanism.
- **Holdout:** verification inputs fixed before repair generation and hidden from the repair proposer.
- **Verdict:** a bounded statement about observations under the recorded contract and execution budget; never a universal safety claim.
