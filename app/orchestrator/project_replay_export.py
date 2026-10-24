"""Owner-only portable replay for a completed approved-project witness.

The bundle contains private source. It is deliberately separate from the
redacted public share path and is never generated from an incomplete result.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from collections.abc import Mapping
from pathlib import Path

from app.domain.comparison import numeric_matches
from app.domain.project_profile import ProfileApproval, ProjectProfileDraft
from app.domain.project_run import ProjectRunSpec
from app.domain.validator import validate_input
from app.providers.e2b_runner import ProjectAdapter, validate_snapshot

_REPLAY_SCRIPT = Path(__file__).with_name("project_replay_script.py")


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _input_digest(value: dict) -> str:
    # Match the worker's persisted JSON input hash exactly.
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _observations(result: dict, contract) -> tuple[dict, dict]:
    if result.get("kind") == "approved_project_search":
        witness = result.get("witness")
        if not isinstance(witness, dict) or witness.get("changed_conclusion") is not True:
            raise ValueError("No repeatable witness is available for replay")
        expected = witness
    elif result.get("kind") == "approved_project_sample":
        expected = result
    else:
        raise ValueError("This project result cannot be replayed")
    if result.get("changed_conclusion") is not True or expected.get("repeatable") is not True:
        raise ValueError("A completed, repeatable changed conclusion is required")
    witness_input = result.get("witness_input")
    if not isinstance(witness_input, dict) or _input_digest(witness_input) != expected.get(
        "input_sha256"
    ):
        raise ValueError("Witness input is absent or does not match its digest")
    if not validate_input(contract, witness_input).valid:
        raise ValueError("Witness input no longer satisfies the approved contract")
    observations = {}
    for key in ("old", "old_repeat", "new", "new_repeat"):
        item = expected.get(key)
        if not isinstance(item, dict):
            raise ValueError("A repeated environment observation is missing")
        ratio = item.get("ratio")
        if type(ratio) not in (int, float) or not math.isfinite(ratio):
            raise ValueError("A recorded measurement is invalid")
        if item.get("conclusion") not in {
            "above_threshold", "below_threshold", "inconclusive"
        }:
            raise ValueError("A recorded conclusion is invalid")
        if item.get("dtype") is not None and not isinstance(item["dtype"], str):
            raise ValueError("A recorded dtype is invalid")
        observations[key] = {
            "ratio": float(ratio), "conclusion": item["conclusion"],
            "dtype": item.get("dtype"),
        }
    if (observations["old"]["conclusion"] == observations["new"]["conclusion"]
            or observations["old"]["conclusion"] != observations["old_repeat"]["conclusion"]
            or observations["new"]["conclusion"] != observations["new_repeat"]["conclusion"]
            or observations["old"]["dtype"] != observations["old_repeat"]["dtype"]
            or observations["new"]["dtype"] != observations["new_repeat"]["dtype"]
            or not numeric_matches(observations["old"]["ratio"],
                                   observations["old_repeat"]["ratio"], contract)
            or not numeric_matches(observations["new"]["ratio"],
                                   observations["new_repeat"]["ratio"], contract)):
        raise ValueError("Recorded repeatability or changed conclusion is inconsistent")
    return witness_input, observations


def build_project_replay_bundle(
    job: dict, profile: dict, files: Mapping[str, bytes],
) -> bytes:
    """Validate immutable evidence and package only the owner's source snapshot."""
    if job.get("status") != "completed" or job.get("job_kind") != "investigation":
        raise ValueError("A completed approved-project investigation is required")
    spec = ProjectRunSpec.model_validate(job["spec_json"])
    result = job.get("result_json")
    if not isinstance(result, dict) or profile.get("status") != "approved":
        raise ValueError("Saved evidence or project approval is missing")
    draft = ProjectProfileDraft.model_validate(profile["draft_json"])
    approval = ProfileApproval.model_validate(profile["approval_json"])
    if (
        str(spec.profile_id) != str(profile["id"])
        or (draft.provider, draft.repository) != (spec.provider, spec.repository)
        or draft.sandbox_provider != spec.sandbox_provider
        or draft.digest != spec.profile_digest
        or draft.digest != profile["profile_digest"]
        or draft.contract_hash != spec.contract_hash
        or draft.contract_hash != profile["contract_hash"]
        or approval.profile_digest != draft.digest
        or approval.old.lock_sha256 != spec.old_environment_lock_sha256
        or approval.new.lock_sha256 != spec.new_environment_lock_sha256
    ):
        raise ValueError("Approved profile does not match the immutable run")
    if (
        result.get("source_sha256") != spec.source_sha256
        or result.get("contract_hash") != spec.contract_hash
        or result.get("base_sha") != spec.base_sha
        or result.get("head_sha") != spec.head_sha
        or result.get("environment_lock_sha256") != [
            spec.old_environment_lock_sha256, spec.new_environment_lock_sha256
        ]
        or validate_snapshot(files) != spec.source_sha256
    ):
        raise ValueError("Saved result or source snapshot does not match approval")
    adapter = ProjectAdapter(draft.adapter_module, draft.adapter_function)
    adapter.validate()
    witness_input, observations = _observations(result, draft.contract)
    record = {
        "schema_version": "1",
        "provider": spec.provider,
        "repository": spec.repository,
        "change_number": spec.change_number,
        "base_sha": spec.base_sha,
        "head_sha": spec.head_sha,
        "source_sha256": spec.source_sha256,
        "profile_digest": spec.profile_digest,
        "contract_hash": spec.contract_hash,
        "sandbox_provider": spec.sandbox_provider,
        "adapter": {"module": adapter.module, "function": adapter.function},
        "environments": {
            "old": approval.old.model_dump(mode="json"),
            "new": approval.new.model_dump(mode="json"),
        },
        "input_sha256": _input_digest(witness_input),
        "witness_input": witness_input,
        "observations": observations,
        "tested_scope": result.get("scope"),
        "other_inputs_inconclusive": bool(result.get("inconclusive")),
    }
    launcher = Path(__file__).resolve().parents[2] / "runner" / "e2b_project.py"
    payload = {f"source/{name}": content for name, content in files.items()}
    payload.update({
        "record.json": _json(record),
        "contract.json": _json(draft.contract.model_dump(mode="json")),
        "adapter.json": _json(record["adapter"]),
        "input.json": _json(witness_input),
        "launcher.py": launcher.read_bytes(),
        "replay.py": _REPLAY_SCRIPT.read_bytes(),
        "README.txt": (
            b"ReproDelta approved-project witness replay\n"
            b"This archive contains the owner's repository source snapshot. Keep it private.\n"
            b"Use isolated Python 3.12 environments with the recorded NumPy pins.\n"
            b"Set REPRODELTA_OLD_PYTHON and REPRODELTA_NEW_PYTHON to those interpreters,\n"
            b"then run: python replay.py\n"
            b"Replay executes the included repository code. Use an isolated computer or VM.\n"
            b"Matching the recorded conclusion is evidence only for this input and these environments;\n"
            b"it does not establish scientific correctness or a general safety claim.\n"
            b"The integrity manifest detects changes, but is not a digital signature.\n"
        ),
    })
    manifest = {
        "schema_version": "1", "job_id": str(job["id"]),
        "files": {name: hashlib.sha256(content).hexdigest()
                  for name, content in sorted(payload.items())},
    }
    payload["manifest.json"] = _json(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(payload.items()):
            entry = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100444 << 16
            archive.writestr(entry, content)
    return output.getvalue()
