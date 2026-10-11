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
- Run mode: local_simulation or pinned_workers (never claims Docker without worker_http)
- Simulated mode flag
- Environment versions from run evidence
- Replay instructions: separate Windows and POSIX commands
- Scope and limitations statement
- Export timestamp and schema version

Also produces a human-readable text summary.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.domain.evidence import InvestigationEvidence

_SCHEMA_VERSION = "2"


def _detect_run_mode(evidence: InvestigationEvidence) -> str:
    """
    Label only runtime versions that the evidence actually establishes.
    """
    if len(evidence.controls) < 2:
        return "unverified"
    old_version = evidence.controls[0].run1.numpy_version or ""
    new_version = evidence.controls[1].run1.numpy_version or ""
    witness_old = evidence.witness.old_run.numpy_version if evidence.witness else old_version
    witness_new = evidence.witness.new_run.numpy_version if evidence.witness else new_version
    versions = (old_version, new_version, witness_old or "", witness_new or "")
    if all(version.startswith("simulated-") for version in versions):
        return "local_simulation"
    if versions == ("1.26.4", "2.0.2", "1.26.4", "2.0.2"):
        return "pinned_workers"
    return "unverified"


def _collect_environment_versions(evidence: InvestigationEvidence) -> dict:
    """Collect numpy version strings from run evidence."""
    versions: dict[str, str] = {}

    if evidence.controls:
        ctrl = evidence.controls[0]
        if ctrl.run1.numpy_version:
            versions["old_numpy_version"] = ctrl.run1.numpy_version
    if len(evidence.controls) > 1:
        ctrl2 = evidence.controls[1]
        if ctrl2.run1.numpy_version:
            versions["new_numpy_version"] = ctrl2.run1.numpy_version

    if evidence.witness is not None:
        if evidence.witness.old_run.numpy_version:
            versions["witness_old_numpy_version"] = evidence.witness.old_run.numpy_version
        if evidence.witness.new_run.numpy_version:
            versions["witness_new_numpy_version"] = evidence.witness.new_run.numpy_version

    return versions


def generate_report(evidence: InvestigationEvidence) -> dict:
    """Return the full JSON evidence bundle."""
    run_mode = _detect_run_mode(evidence)
    simulated = run_mode == "local_simulation"

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
        # --- run mode ---
        "run_mode": run_mode,
        "simulated_mode": simulated,
        "environment_versions": _collect_environment_versions(evidence),
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
        "replay_commands": generate_replay_commands(evidence),
        "replay_instructions": generate_replay_instructions(evidence),
        # --- limitations ---
        "limitations": generate_limitations(evidence),
    }


def generate_replay_commands(evidence: InvestigationEvidence) -> dict:
    """Return separate Windows and POSIX replay commands."""
    inv_id = evidence.investigation_id
    run_mode = _detect_run_mode(evidence)
    simulated = run_mode == "local_simulation"

    if evidence.witness is not None:
        w = evidence.witness
        input_json_str = json.dumps(w.input_json, separators=(",", ":"))
        old_ratio = w.old_run.ratio
        old_conclusion = w.old_run.conclusion or "?"
        new_ratio = w.new_run.ratio
        new_conclusion = w.new_run.conclusion or "?"
        ratio_str_old = f"{old_ratio:.11f}" if old_ratio is not None else "?"
        ratio_str_new = f"{new_ratio:.11f}" if new_ratio is not None else "?"

        posix_json = input_json_str.replace("'", "'\"'\"'")
        windows_json = input_json_str.replace("'", "''")
        note = "# Original investigation used local simulation. These commands run real NumPy.\n" if simulated else ""
        posix_witness = (
            f"{note}printf '%s' '{posix_json}' | uv run --no-project --python 3.12 --with numpy==1.26.4 python fixture/project/run.py\n"
            f"# Recorded old result: ratio={ratio_str_old}, conclusion={old_conclusion}\n"
            f"printf '%s' '{posix_json}' | uv run --no-project --python 3.12 --with numpy==2.0.2 python fixture/project/run.py\n"
            f"# Recorded new result: ratio={ratio_str_new}, conclusion={new_conclusion}"
        )
        windows_witness = (
            f"{note}'{windows_json}' | uv run --no-project --python 3.12 --with numpy==1.26.4 python fixture/project/run.py\n"
            f"# Recorded old result: ratio={ratio_str_old}, conclusion={old_conclusion}\n"
            f"'{windows_json}' | uv run --no-project --python 3.12 --with numpy==2.0.2 python fixture/project/run.py\n"
            f"# Recorded new result: ratio={ratio_str_new}, conclusion={new_conclusion}"
        )
    else:
        posix_witness = "# No witness was found within the search budget."
        windows_witness = "# No witness was found within the search budget."

    contract_hash = evidence.contract_hash
    posix = (
        f"# ReproDelta replay — investigation {inv_id}\n"
        f"# Contract hash: {contract_hash}\n"
        "# Run from a clean repository checkout with uv installed.\n"
        f"{posix_witness}"
    )
    windows = (
        f"# ReproDelta replay — investigation {inv_id}\n"
        f"# Contract hash: {contract_hash}\n"
        "# Run from a clean repository checkout with uv installed.\n"
        f"{windows_witness}"
    )
    return {"posix": posix, "windows": windows}


def generate_replay_instructions(evidence: InvestigationEvidence) -> str:
    """Return shell commands that reproduce the witness observation (POSIX)."""
    cmds = generate_replay_commands(evidence)
    return cmds["posix"]


def generate_limitations(evidence: InvestigationEvidence) -> list[str]:
    """Return a list of limitation statements for this evidence bundle."""
    limitations: list[str] = [
        "This is a constructed demonstration case, not an independently observed research defect.",
    ]

    run_mode = _detect_run_mode(evidence)
    if run_mode == "local_simulation":
        limitations.append(
            "This demonstration used simulated precision behavior — "
            "NumPy version differences were emulated in-process, not in isolated containers."
        )
        limitations.append(
            "Run mode: local_simulation. "
            "No Docker isolation was used. "
            "For isolated reproduction, use RUNNER_MODE=worker_http with pinned container images."
        )
    elif run_mode == "unverified":
        limitations.append(
            "The report does not establish both pinned NumPy runtime versions; run mode is unverified."
        )

    # Distinguish mock proposals from calls that produced no proposal.
    if not evidence.model_proposals:
        limitations.append(
            "No model proposal was recorded; a live Nebius/Nemotron response was not confirmed."
        )
    elif any(getattr(p, "is_mock", False) for p in evidence.model_proposals):
        limitations.append("At least one model proposal was generated in mock mode.")

    limitations.append(
        "Repair checks use predefined fixture variants; the model's proposed diff was not applied."
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
    run_mode = _detect_run_mode(evidence)
    simulated = run_mode == "local_simulation"

    lines.append("=" * 72)
    lines.append("REPRODELTA — INVESTIGATION SUMMARY")
    lines.append("=" * 72)
    lines.append(f"Investigation : {evidence.investigation_id}")
    lines.append(f"Project       : {evidence.project_name}")
    lines.append(f"Contract hash : {evidence.contract_hash}")
    lines.append(f"Timestamp     : {evidence.timestamp.isoformat()}")
    lines.append(f"Verdict       : {evidence.verdict.value}")
    lines.append(f"Scope         : {evidence.scope}")
    lines.append(f"Run mode      : {run_mode}")
    if simulated:
        lines.append("  (!) Simulation mode — no Docker isolation; NumPy behavior emulated in-process")
    lines.append("")

    # Model call status
    is_mock = any(getattr(p, "is_mock", False) for p in evidence.model_proposals)
    if not evidence.model_proposals:
        lines.append("MODEL CALLS: none made (no model calls attempted)")
    elif is_mock:
        lines.append("MODEL CALLS: [MOCK_LLM_MODE] — no live Nebius/Nemotron call made")
    else:
        lines.append("MODEL CALLS: [LIVE] — real Nebius API calls were made")
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

    # Environment versions
    env_versions = _collect_environment_versions(evidence)
    if env_versions:
        lines.append("ENVIRONMENT VERSIONS")
        lines.append("-" * 40)
        for k, v in env_versions.items():
            lines.append(f"  {k}: {v}")
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
    lines.append("REPLAY INSTRUCTIONS (POSIX)")
    lines.append("-" * 40)
    lines.append(generate_replay_instructions(evidence))

    lines.append("REPLAY INSTRUCTIONS (WINDOWS)")
    lines.append("-" * 40)
    cmds = generate_replay_commands(evidence)
    lines.append(cmds["windows"])

    return "\n".join(lines)
