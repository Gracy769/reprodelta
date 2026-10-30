"""Integrity and non-regression verification for ReproDelta fixtures."""

from __future__ import annotations

import hashlib
import tomllib
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

CANONICAL_BYTE_HASHES = {
    "least_squares.py": "70f18668e3a17edef7d2eab69b1c76d522422cb394a655ee05ba874f3ab67013",
    "pipeline.py": "8ff7c1cbc56581d63229e43c5e67a63aa46e3c2d043b35063c853bcbd8b770af",
    "run.py": "0f5ed64c0e4c8354c12395dada029801318857513dfb46db8e185684235b9abd",
    "sensor.py": "7b950148bbe4691fcf9d819a8e39795181eb1b64eacc7135a88110ee42166524",
    "evaluation/isce3_mask_shift_repro.py": "449c00a23e0d4b92a7c925d7a36142fed44e6b63ea4caab7e8d72f7b5d16e3b7",
}


def test_fixture_project_sources_and_canonical_digest() -> None:
    """Ensure fixture project sources exist and match both canonical text and raw byte hashes."""
    sources = project_sources(FIXTURE_PROJECT)
    expected_files = {"pipeline.py", "run.py", "sensor.py", "least_squares.py"}
    assert set(sources.keys()) == expected_files
    digest = project_digest(sources)
    assert digest == CANONICAL_DIGEST, (
        f"Fixture project digest mismatch: expected {CANONICAL_DIGEST}, got {digest}. "
        "The curated fixture must remain immutable to preserve test witness contracts."
    )

    # Byte-level integrity check verifying raw bytes for all fixture files
    for filename in sorted(expected_files):
        file_path = FIXTURE_PROJECT / filename
        assert file_path.is_file(), f"Fixture file {filename} must exist"
        raw_bytes = file_path.read_bytes().replace(b"\r\n", b"\n")
        file_digest = hashlib.sha256(raw_bytes).hexdigest()
        assert file_digest == CANONICAL_BYTE_HASHES[filename], (
            f"Raw byte digest mismatch for {filename}: expected {CANONICAL_BYTE_HASHES[filename]}, "
            f"got {file_digest}. Byte-level fixture alterations are forbidden."
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
    # Ensure both hunks were applied cleanly and no targeted old expression remains
    assert patched.count("sample / np.float32(2.0)") == 2, (
        "Patched source must contain both replacements of 'sample / np.float32(2.0)'"
    )
    assert "sample / 2.0" not in patched, (
        "Patched source must not contain any remaining unpatched 'sample / 2.0' expressions"
    )


def test_isce3_mask_evaluation_behavior() -> None:
    """Verify that isce3 mask shift reproduction demonstrates expected defect, repair, and byte integrity."""
    repro_path = ROOT / "evaluation" / "isce3_mask_shift_repro.py"
    assert repro_path.is_file(), "isce3_mask_shift_repro.py must exist"
    repro_bytes = repro_path.read_bytes().replace(b"\r\n", b"\n")
    repro_digest = hashlib.sha256(repro_bytes).hexdigest()
    assert repro_digest == CANONICAL_BYTE_HASHES["evaluation/isce3_mask_shift_repro.py"], (
        f"Evaluation reproduction byte digest mismatch: expected "
        f"{CANONICAL_BYTE_HASHES['evaluation/isce3_mask_shift_repro.py']}, got {repro_digest}."
    )

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


def test_pyproject_protects_fixtures_and_configures_test_hygiene() -> None:
    """Ensure pyproject.toml explicitly excludes fixture/project from lint modification."""
    pyproject_path = ROOT / "pyproject.toml"
    assert pyproject_path.exists()
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    ruff_conf = data.get("tool", {}).get("ruff", {})
    assert "fixture/project" in ruff_conf.get("extend-exclude", []), (
        "pyproject.toml must configure extend-exclude = ['fixture/project'] to prevent "
        "accidental formatting changes to immutable fixture sources."
    )

    pytest_conf = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    assert pytest_conf.get("asyncio_default_fixture_loop_scope") == "function"
    assert any("httpx" in w for w in pytest_conf.get("filterwarnings", []))
