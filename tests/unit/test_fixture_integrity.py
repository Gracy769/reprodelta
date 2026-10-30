"""Integrity and non-regression verification for ReproDelta fixtures."""

from __future__ import annotations

from pathlib import Path

from app.domain.contract import FIXTURE_CONTRACT, hash_contract
from app.domain.project_profile import ProjectProfileDraft
from app.orchestrator.patch_verifier import extract_diff
from app.providers.nebius import MOCK_RESPONSES
from evaluation.isce3_mask_shift_repro import pack_mask
from runner.patching import apply_diff, project_digest, project_sources

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PROJECT = ROOT / "fixture" / "project"
CANONICAL_DIGEST = "9bb6fb8e833cbe2d091d37d96d0229a0daa4dd1b45a9a208bf814fce83eff8f1"
CANONICAL_CONTRACT_HASH = "e1d68eeb3ecb1a729e34f8bad85fe65824945e565a23d20f1e953e3ee0d4affd"


def test_fixture_project_sources_and_canonical_digest() -> None:
    """Ensure fixture project sources exist and match the canonical hash."""
    sources = project_sources(FIXTURE_PROJECT)
    assert set(sources.keys()) == {"pipeline.py", "run.py", "sensor.py", "least_squares.py"}
    digest = project_digest(sources)
    assert digest == CANONICAL_DIGEST, (
        f"Fixture project digest mismatch: expected {CANONICAL_DIGEST}, got {digest}. "
        "The curated fixture must remain immutable to preserve test witness contracts."
    )


def test_default_contract_hash_immutable() -> None:
    """Ensure default fixture contract hash has not drifted."""
    contract_hash = hash_contract(FIXTURE_CONTRACT)
    assert contract_hash == CANONICAL_CONTRACT_HASH, (
        f"Contract hash mismatch: expected {CANONICAL_CONTRACT_HASH}, got {contract_hash}."
    )


def test_model_patch_applies_cleanly() -> None:
    """Ensure the canonical mock repair patch applies to the immutable pipeline source."""
    sources = project_sources(FIXTURE_PROJECT)
    pipeline_source = sources["pipeline.py"]
    diff = extract_diff(MOCK_RESPONSES["patch_proposal"])

    patched = apply_diff(pipeline_source, diff)
    assert "np.float32(2.0)" in patched


def test_isce3_mask_evaluation_behavior() -> None:
    """Verify that isce3 mask shift reproduction demonstrates the expected defect and repair."""
    # Control input (0, 0) should produce identical values regardless of repair
    control_unrepaired = pack_mask(0, 0, repaired=False)
    control_repaired = pack_mask(0, 0, repaired=True)
    assert control_unrepaired == 5
    assert control_repaired == 5

    # Target witness input (3, 1)
    repaired_val = pack_mask(3, 1, repaired=True)
    assert repaired_val == 196869
    assert repaired_val > 65535


def test_isce3_mask_profile_validity() -> None:
    """Ensure the isce3 mask project profile json schema is valid and loadable."""
    profile_path = ROOT / "evaluation" / "isce3-mask-project-profile.json"
    assert profile_path.exists()
    profile = ProjectProfileDraft.model_validate_json(profile_path.read_text(encoding="utf-8"))
    assert profile.repository == "Gracy769/reprodelta-isce3-mask-evaluation"
    assert profile.contract.output_selector.field_name == "packed_mask"
    assert profile.contract.conclusion.threshold == 65535
