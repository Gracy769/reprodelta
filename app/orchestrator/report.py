"""
Evidence report generator.

Produces a self-contained JSON bundle from InvestigationEvidence.
The bundle includes:
- Investigation metadata (id, timestamp, project, contract hash)
- Environment digests (old/new environment manifests)
- Input artifact hash (witness input)
- Contract body (full JSON)
- Control run results (old/old, new/new repeatability)
- Witness: exact input values, outputs, conclusions, ratios, dtypes
- Witness reduction history
- Model proposals (with mock/live flag)
- Intervention: description, prediction, result, assessment
- Patch diffs and verification results (per holdout)
- Replay instructions (commands to reproduce)
- Scope and limitations statement
- Export timestamp and schema version

Also produces a human-readable text summary.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.domain.evidence import InvestigationEvidence

_SCHEMA_VERSION = "1"


def generate_report(evidence: InvestigationEvidence) -> dict:
    """Return the full JSON evidence bundle."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "export_timestamp": datetime.now(UTC).isoformat(),
        # --- metadata ---
        "investigation_id": evidence.investigation_id,
        "timestamp": evidence.timestamp.isoformat(),
        "project_name": evidence.project_name,
        "contract_hash": evidence.contract_hash,
        "contract_body": evidence.contract_body.model_dump(mode="json"),
        "verdict": evidence.verdict.value,
        "scope": evidence.scope,
        # --- controls ---
        "controls": [c.model_dump(mode="json") for c in evidence.controls],
        # --- witness ---
        "witness": evidence.witness.model_dump(mode="json") if evidence.witness else None,
        # --- reduction ---
        "reduction": (
            evidence.reduction.model_dump(mode="json")  # type: ignore[union-attr]
            if evidence.reduction is not None
            else None
        ),
        # --- model proposals ---
        "model_proposals": [
            p.model_dump(mode="json")  # type: ignore[union-attr]
            for p in evidence.model_proposals
        ],
        # --- intervention ---
        "intervention": (
            evidence.intervention.model_dump(mode="json")  # type: ignore[union-attr]
            if evidence.intervention is not None
            else None
        ),
        # --- patch results ---
        "patch_results": [
            r.model_dump(mode="json")  # type: ignore[union-attr]
            for r in evidence.patch_results
        ],
        # --- replay ---
        "replay_instructions": generate_replay_instructions(evidence),
        # --- limitations ---
        "limitations": generate_limitations(evidence),
    }


def generate_replay_instructions(evidence: InvestigationEvidence) -> str:
    """Return shell commands that reproduce the witness observation."""
    inv_id = evidence.investigation_id
    contract_hash = evidence.contract_hash

    if evidence.witness is not None:
        w = evidence.witness
        input_json_str = json.dumps(w.input_json, separators=(",", ":"))
        old_ratio = w.old_run.ratio
        old_conclusion = w.old_run.conclusion or "?"
        new_ratio = w.new_run.ratio
        new_conclusion = w.new_run.conclusion or "?"

        ratio_str_old = f"{old_ratio:.11f}" if old_ratio is not None else "?"
        ratio_str_new = f"{new_ratio:.11f}" if new_ratio is not None else "?"

        witness_block = f"""
# 3. Run the witness input in the old environment
echo '{input_json_str}' | python run.py
# Expected: ratio={ratio_str_old}, conclusion={old_conclusion}

# 4. Install NumPy 2.0 (new environment)
pip install numpy==2.0.2

# 5. Run the same witness input in the new environment
echo '{input_json_str}' | python run.py
# Expected: ratio={ratio_str_new}, conclusion={new_conclusion}
"""
    else:
        witness_block = """
# 3. No witness was found within the search budget.
#    Try increasing max_candidates and re-running the investigation.
"""

    return f"""# Agent Replay Lab — Replay Instructions
# Investigation: {inv_id}
# Contract hash: {contract_hash}

# 1. Set up the environment
cd fixture/project

# 2. Install NumPy 1.26 (old environment)
pip install numpy==1.26.4
{witness_block}"""


def generate_limitations(evidence: InvestigationEvidence) -> list[str]:
    """Return a list of limitation statements for this evidence bundle."""
    limitations: list[str] = [
        "This is a constructed demonstration case, not an independently observed research defect.",
    ]

    # Check if any proposal was generated in mock mode
    is_mock = any(
        getattr(p, "is_mock", False) for p in evidence.model_proposals
    )
    if is_mock or len(evidence.model_proposals) == 0:
        limitations.append(
            "Model proposals were generated in mock mode; "
            "a live Nebius/Nemotron call was not made."
        )

    if evidence.witness is not None:
        limitations.append(
            "The observed conclusion change applies to the specific input and tested contract; "
            "it does not constitute a general safety determination."
        )

    limitations.append(
        "An inconclusive result or no finding within the search budget is not evidence of absence."
    )

    return limitations


def generate_text_summary(evidence: InvestigationEvidence) -> str:
    """Return a human-readable plain-text summary of the investigation."""
    lines: list[str] = []

    lines.append("=" * 72)
    lines.append("AGENT REPLAY LAB — INVESTIGATION SUMMARY")
    lines.append("=" * 72)
    lines.append(f"Investigation : {evidence.investigation_id}")
    lines.append(f"Project       : {evidence.project_name}")
    lines.append(f"Contract hash : {evidence.contract_hash}")
    lines.append(f"Timestamp     : {evidence.timestamp.isoformat()}")
    lines.append(f"Verdict       : {evidence.verdict.value}")
    lines.append(f"Scope         : {evidence.scope}")
    lines.append("")

    # Controls
    lines.append("REPEATABILITY CONTROLS")
    lines.append("-" * 40)
    for i, ctrl in enumerate(evidence.controls):
        repeatable = "PASS" if ctrl.repeatability_confirmed else "FAIL"
        lines.append(
            f"  Control {i + 1}  env={ctrl.run1.environment}"
            f"  run1={ctrl.run1.conclusion or '?'}"
            f"  run2={ctrl.run2.conclusion or '?'}"
            f"  repeatable={repeatable}"
        )
    lines.append("")

    # Witness
    if evidence.witness is not None:
        w = evidence.witness
        lines.append("WITNESS")
        lines.append("-" * 40)
        lines.append(f"  Input SHA-256 : {w.input_sha256}")
        sig = w.input_json.get("signal", [])
        lines.append(f"  Signal values : {sig}")
        lines.append(
            f"  Old env : numpy={w.old_run.numpy_version or '?'}"
            f"  dtype={w.old_run.intermediate_dtype or '?'}"
            f"  ratio={w.old_run.ratio}"
            f"  conclusion={w.old_run.conclusion or '?'}"
        )
        lines.append(
            f"  New env : numpy={w.new_run.numpy_version or '?'}"
            f"  dtype={w.new_run.intermediate_dtype or '?'}"
            f"  ratio={w.new_run.ratio}"
            f"  conclusion={w.new_run.conclusion or '?'}"
        )
        lines.append(f"  Conclusion changed : {w.conclusion_changed}")
    else:
        lines.append("WITNESS")
        lines.append("-" * 40)
        lines.append("  No conclusion-changing input found within the search budget.")
    lines.append("")

    # Model proposals
    if evidence.model_proposals:
        lines.append("MODEL PROPOSALS")
        lines.append("-" * 40)
        for i, p in enumerate(evidence.model_proposals):
            mock_flag = "[MOCK]" if getattr(p, "is_mock", False) else "[LIVE]"
            model_id = getattr(p, "model_id", "?")
            lines.append(f"  Proposal {i + 1}  {mock_flag}  model={model_id}")
        lines.append("")

    # Intervention
    if evidence.intervention is not None:
        intv = evidence.intervention
        lines.append("INTERVENTION")
        lines.append("-" * 40)
        lines.append(f"  Assessment : {getattr(intv, 'assessment', '?')}")
        lines.append(f"  Prediction supported : {getattr(intv, 'prediction_supported', '?')}")
        lines.append("")

    # Patch results
    if evidence.patch_results:
        lines.append("PATCH VERIFICATION")
        lines.append("-" * 40)
        for pr in evidence.patch_results:
            kind = getattr(pr, "patch_kind", "?")
            verdict = getattr(pr, "verdict", "?")
            details = getattr(pr, "details", "")
            lines.append(f"  Patch [{kind}]  verdict={verdict}  {details}")
        lines.append("")

    # Limitations
    lines.append("LIMITATIONS")
    lines.append("-" * 40)
    for lim in generate_limitations(evidence):
        lines.append(f"  • {lim}")
    lines.append("")

    # Replay instructions
    lines.append("REPLAY INSTRUCTIONS")
    lines.append("-" * 40)
    lines.append(generate_replay_instructions(evidence))

    return "\n".join(lines)
