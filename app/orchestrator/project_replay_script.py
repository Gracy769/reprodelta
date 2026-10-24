"""Standalone replay script placed in an owner-only approved-project ZIP.

Run ``python replay.py`` only in an isolated environment: this executes the
included repository code under two user-supplied Python interpreters.
"""

from __future__ import annotations

import hashlib
import json
import math
import operator
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OPERATORS = {
    "gt": operator.gt, "gte": operator.ge, "lt": operator.lt,
    "lte": operator.le, "eq": operator.eq, "ne": operator.ne,
}


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _verify_manifest() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    expected = manifest["files"]
    actual = set()
    for path in ROOT.rglob("*"):
        if path.is_symlink():
            raise AssertionError("Bundle contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(ROOT).as_posix())
    assert actual == set(expected) | {"manifest.json"}, "Bundle files changed"
    for name, digest in expected.items():
        path = ROOT / name
        assert path.resolve().is_relative_to(ROOT), "Bundle path escapes archive"
        assert _digest(path.read_bytes()) == digest, f"Bundle file changed: {name}"


def _source_digest() -> str:
    source = ROOT / "source"
    digest = hashlib.sha256()
    files = [path for path in source.rglob("*") if path.is_file()]
    assert files, "Source snapshot is empty"
    for path in sorted(files, key=lambda item: item.relative_to(source).as_posix()):
        name = path.relative_to(source).as_posix()
        content = path.read_bytes()
        digest.update(name.encode("utf-8") + b"\x00" + len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def _child_env() -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "TMPDIR")
    return {**{key: os.environ[key] for key in allowed if key in os.environ},
            "PYTHONNOUSERSITE": "1"}


def _runtime(interpreter: str, expected: dict) -> None:
    result = subprocess.run(
        [interpreter, "-I", "-c",
         "import json,sys,numpy; print(json.dumps({'python': list(sys.version_info[:2]), "
         "'numpy': numpy.__version__}))"],
        capture_output=True, text=True, timeout=20, env=_child_env(), check=True,
    )
    runtime = json.loads(result.stdout)
    assert runtime["python"] == [3, 12], "Python 3.12 is required"
    assert runtime["numpy"] == expected["numpy_version"], "NumPy pin differs from approval"
    lock = f"python==3.12\nnumpy=={expected['numpy_version']}\n".encode()
    assert _digest(lock) == expected["lock_sha256"], "Environment lock marker differs"


def _evaluate(output: dict, contract: dict) -> dict:
    result = output["result"]
    field = contract["output_selector"]["field_name"]
    value = result[field]
    assert type(value) in (int, float) and math.isfinite(value), "Invalid numeric output"
    ratio = float(value)
    rule = contract["conclusion"]
    threshold = rule["threshold"]
    if ratio == threshold:
        conclusion = {
            "above": "above_threshold", "below": "below_threshold",
            "inconclusive": "inconclusive",
        }[rule["tie_behavior"]]
    else:
        conclusion = ("above_threshold" if OPERATORS[rule["operator"]](ratio, threshold)
                      else "below_threshold")
    return {"ratio": ratio, "conclusion": conclusion,
            "dtype": result.get("intermediate_dtype")}


def _compare(actual: dict, expected: dict, contract: dict) -> None:
    policy = contract["comparison"]
    tolerance = policy["atol"] + policy["rtol"] * abs(expected["ratio"])
    assert actual["conclusion"] == expected["conclusion"], "Conclusion did not reproduce"
    assert abs(actual["ratio"] - expected["ratio"]) <= tolerance, "Measurement drifted"
    if expected["dtype"] is not None:
        assert actual["dtype"] == expected["dtype"], "Recorded dtype did not reproduce"


def main() -> None:
    _verify_manifest()
    record = json.loads((ROOT / "record.json").read_text(encoding="utf-8"))
    contract = json.loads((ROOT / "contract.json").read_text(encoding="utf-8"))
    witness = json.loads((ROOT / "input.json").read_text(encoding="utf-8"))
    assert _source_digest() == record["source_sha256"], "Source snapshot digest changed"
    assert _digest(_canonical(contract)) == record["contract_hash"], "Contract changed"
    assert _digest(json.dumps(witness, sort_keys=True, allow_nan=False).encode()) == record[
        "input_sha256"
    ], "Witness input changed"
    assert witness == record["witness_input"], "Recorded input differs"
    for environment in ("old", "new"):
        interpreter = os.environ.get(f"REPRODELTA_{environment.upper()}_PYTHON")
        assert interpreter, f"Set REPRODELTA_{environment.upper()}_PYTHON"
        _runtime(interpreter, record["environments"][environment])
        for repeat in (False, True):
            result = subprocess.run(
                [interpreter, "-I", "-B", str(ROOT / "launcher.py"),
                 str(ROOT / "source"), str(ROOT / "input.json"),
                 str(ROOT / "adapter.json")],
                capture_output=True, text=True, timeout=min(
                    30, contract["resource_limits"]["max_wall_seconds"]
                ), cwd=ROOT / "source", env=_child_env(), check=True,
            )
            output = json.loads(result.stdout)
            observed = _evaluate(output, contract)
            key = environment + ("_repeat" if repeat else "")
            _compare(observed, record["observations"][key], contract)
    old = record["observations"]["old"]["conclusion"]
    new = record["observations"]["new"]["conclusion"]
    assert old != new, "Recorded witness is not a changed conclusion"
    print(f"Replay matched the recorded witness: {old} -> {new}")
    print("Scope: one recorded input and two approved NumPy environments; no general verdict.")


if __name__ == "__main__":
    main()
