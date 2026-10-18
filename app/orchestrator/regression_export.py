"""Portable trusted-fixture regression replay; no arbitrary source packaging."""

import hashlib
import io
import json
import math
import zipfile
from pathlib import Path

from app.domain.contract import FIXTURE_CONTRACT, hash_contract
from runner.patching import project_digest, project_sources

REPLAY_TEST = '''"""Run: python -m pytest test_regression.py -q (two pinned interpreters required)."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def test_recorded_dependency_regression():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        path = ROOT / name
        assert not path.is_symlink(), "Symlinks are forbidden"
        assert path.resolve().is_relative_to(ROOT), "Path escapes bundle"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, "Artifact hash mismatch: " + name
    data = json.loads((ROOT / "observations.json").read_text(encoding="utf-8"))
    for kind, version in (("old", "1.26.4"), ("new", "2.0.2")):
        interpreter = os.environ.get("REPRODELTA_" + kind.upper() + "_PYTHON")
        assert interpreter, "Set REPRODELTA_" + kind.upper() + "_PYTHON to the pinned interpreter"
        child_env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
        result = subprocess.run([interpreter, "-B", str(ROOT / "project" / "run.py")],
            input=json.dumps(data["input"]), capture_output=True, text=True,
            timeout=30, cwd=ROOT / "project", env=child_env)
        assert result.returncode == 0, "Replay execution failed: " + result.stderr[:1000]
        output = json.loads(result.stdout)["result"]
        expected = data[kind]
        assert output["numpy_version"] == version, "Wrong NumPy runtime"
        assert output["ratio"] == expected["ratio"], "Recorded numerical observation changed"
        assert output["intermediate_dtype"] == expected["intermediate_dtype"]
        value = output["ratio"]
        conclusion = "inconclusive" if value == 0.5 else "above_threshold" if value > 0.5 else "below_threshold"
        assert conclusion == expected["conclusion"], "Declared conclusion changed"
'''


def replay_material(evidence) -> dict | None:
    witness = evidence.witness
    if not witness or hash_contract(evidence.contract_body) != hash_contract(FIXTURE_CONTRACT):
        return None
    sources = project_sources(Path(__file__).resolve().parents[2] / "fixture/project")
    digest = project_digest(sources)
    if any(run.status != "ok" or run.base_project_sha256 != digest
           for run in (witness.old_run, witness.new_run)):
        return None
    return {"project_sha256": digest, "sources": sources}


def build_regression_bundle(report: dict) -> bytes:
    if report.get("run_mode") != "pinned_workers" or report.get("simulated_mode"):
        raise ValueError("A real pinned-worker investigation is required")
    witness, material = report.get("witness"), report.get("replay_material")
    if not witness or not material or report.get("contract_hash") != hash_contract(FIXTURE_CONTRACT):
        raise ValueError("Historical or incomplete source evidence cannot produce a regression bundle")
    sources = material["sources"]
    if set(sources) != {"pipeline.py", "run.py", "sensor.py", "least_squares.py"}:
        raise ValueError("Unsupported source manifest")
    if project_digest(sources) != material["project_sha256"]:
        raise ValueError("Source evidence hash mismatch")
    if hash_contract(FIXTURE_CONTRACT) != hashlib.sha256(json.dumps(report["contract_body"], sort_keys=True, separators=(",", ":")).encode()).hexdigest():
        raise ValueError("Contract evidence hash mismatch")
    input_digest = hashlib.sha256(json.dumps(witness["input_json"], sort_keys=True).encode()).hexdigest()
    if input_digest != witness["input_sha256"]:
        raise ValueError("Witness input hash mismatch")
    for kind, version in (("old", "1.26.4"), ("new", "2.0.2")):
        run = witness[kind + "_run"]
        if (run.get("status") != "ok" or run.get("numpy_version") != version
                or run.get("base_project_sha256") != material["project_sha256"]
                or type(run.get("ratio")) not in (int, float) or not math.isfinite(run["ratio"])):
            raise ValueError("Missing or invalid pinned witness evidence")
    observations = {"input": witness["input_json"], "old": witness["old_run"], "new": witness["new_run"]}
    files = {"project/" + name: source.encode() for name, source in sources.items()}
    files.update({"observations.json": json.dumps(observations, sort_keys=True).encode(),
                  "contract.json": json.dumps(report["contract_body"], sort_keys=True).encode(),
                  "test_regression.py": REPLAY_TEST.encode(),
                  "README.txt": b"ReproDelta recorded-regression replay\nSet REPRODELTA_OLD_PYTHON and REPRODELTA_NEW_PYTHON to Python 3.12 interpreters with NumPy 1.26.4 and 2.0.2 respectively.\nRun python -m pytest test_regression.py -q. This test preserves the recorded difference; it does not certify a repair or scientific correctness.\nIntegrity hashes detect changed contents; they are not a digital signature. Verify the ZIP SHA-256 against the original download header.\n"})
    manifest = {"schema_version": "1", "investigation_id": report["investigation_id"],
                "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}}
    files["manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, content)
    return output.getvalue()
