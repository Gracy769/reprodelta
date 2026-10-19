# Design system and product interaction

**Status:** implemented visual direction for the evidence workbench; later workflow features below remain proposed where the app does not yet implement them. The interface must let a reviewer see what was observed, what was inferred, and what remains unknown.

## Information architecture

1. **Investigations:** list with project, upgrade, status, last activity, and spend against budget.
2. **Setup:** project snapshot → environment comparison → contract review → run budget. Start is disabled until the contract is approved and a runner preflight succeeds.
3. **Live run:** progress by stage, run counts, rejected invalid candidates, elapsed time, cancellation, and failure details.
4. **Evidence:** paired outputs, conclusion change, witness, reduction history, trace boundary, intervention, and source citations.
5. **Repair:** proposed diff, frozen checks, performance/dtype impact, and reviewer decision.
6. **Export:** human report plus machine-readable manifest and replay instructions.

## Screen behavior

### Contract review

Show each field as a separate inspectable row: input domain, shape/dtype, output selector, decision rule, tolerance, allowed resource overhead, and known assumptions. Mark model suggestions as **Proposed** until approved. Require an explicit approval action that records reviewer and contract version. After approval, create a new contract version for edits; never silently change the active one during a run.

### Evidence view

Lead with a sentence such as “For this valid input, version A concludes **below threshold** and version B concludes **above threshold**.” Next show the exact values and threshold, then expandable trace and raw logs. Place “Observed,” “Supported explanation,” and “Unknown” in separate sections. An intervention card states the changed line/configuration, predicted effect, measured effect, and alternative explanations still possible. Every graph has a table equivalent and units on axes.

### Repair view

Keep the patch proposal visually separate from verification. The verifier panel lists each frozen check with `pass`, `fail`, `not run`, or `inconclusive`, plus reason and artifact. A patch cannot receive “passes stated checks” if a required check is missing. Show a prominent “Review and apply manually” action; no default auto-merge.

## Visual tokens

The implemented palette uses a pale grid canvas, white evidence panels, thin dividers, bold green actions, oversized headings, and monospace metadata. It adapts the visual rhythm of the public [GitHub Universe site](https://githubuniverse.com/) to ReproDelta's own workflow and content. Reserve explicit blue, amber, and red states for evidence, caveats, and failures; labels must carry the meaning independently of color.

| Token | Value | Use |
| --- | --- | --- |
| `surface` | `#F2F5F3` | main canvas |
| `surface-muted` | `#E8EEEA` | grouped evidence |
| `panel` | `#FFFFFF` | content panels |
| `text` | `#101915` | primary text |
| `text-muted` | `#4A5A50` | secondary text |
| `border` | `#CBD5CD` | separators and inputs |
| `accent` | `#087C2A` | primary action and links |
| `focus` | `#125EAE` | keyboard focus ring |
| `observed` | `#215B9F` | measured evidence |
| `supported` | `#0D6731` | supported, bounded result |
| `warning` | `#925000` | limitations and inconclusive states |
| `failure` | `#A33324` | failed check or setup error |

Typography: system sans for interface; monospace for values, hashes, code, and environment identifiers. Hero headings use a tight line height and large responsive scale; body text remains 16 px with line height above 1.5. Spacing scale: 4, 8, 12, 16, 24, 32 px. Use a maximum reading width near 80 characters for explanatory text and allow wide scrollable panels for traces and diffs. Avoid color-only meaning; every state includes a label and icon/text description. Motion is limited to a short entrance, hover feedback, and a small cursor blink, all disabled under `prefers-reduced-motion`.

## Components

`VerdictTag` displays the exact bounded verdict and links to its evidence; `ContractField` shows proposal, provenance, approval, and validation; `RunTimeline` shows completed/pending/failed stages; `EnvironmentDiff` shows direct and transitive package changes; `WitnessViewer` compares old/new output with units and tolerances; `InterventionCard` contrasts prediction with observation; `VerificationMatrix` lists frozen checks; `SourceCitation` links to retrieved documentation with access time and excerpt hash. Component props and API signatures remain implementation decisions, not existing interfaces.

## Interaction and accessibility rules

- Keyboard focus must be visible, with a clear reading and tab order. Do not rely on hover to reveal the only action. [W3C focus guidance](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible)
- Meet WCAG 2.2 AA text contrast, including 4.5:1 for ordinary text, and check non-text focus/controls separately. [W3C contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum)
- Announce stage changes and completed results through accessible status messages without moving focus unexpectedly. [W3C status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html)
- Tables of numerical data must preserve precision, units, dtype, and NaN/Inf explicitly. Do not round values so far that a threshold crossing disappears.
- Loading and failure states say what is happening or what the user can do. “No witness found” includes budget and tested scope.
- Mobile can support review, but setup, trace, and diff views target a desktop viewport first. Preserve horizontal access to wide data rather than shrinking code to illegibility.

## Copy rules

Use “proposed by model” for generated properties and patches; “observed in runs” for execution facts; “supports mechanism” for intervention evidence. Do not say “proved,” “safe,” or “root cause” without a narrowly defined proof. Show the contract version and environment hashes near every exported verdict.
