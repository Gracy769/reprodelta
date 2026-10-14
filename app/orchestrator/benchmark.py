"""Seeded equal-budget comparisons; preserve raw observations, not success claims."""

from __future__ import annotations

import hashlib
import json
import random

import numpy as np

from app.domain.contract import (
    FIXTURE_CONTRACT,
    ConclusionRule,
    InputField,
    InputSchema,
    hash_contract,
)
from app.domain.types import VerdictKind
from app.orchestrator.budget import BudgetExceeded
from app.orchestrator.executor import compare_outputs
from app.providers.runner import RunManifest, RunnerAdapter, make_run_id

SENSOR_CONTRACT = FIXTURE_CONTRACT.model_copy(
    update={
        "conclusion": ConclusionRule(operator="gt", threshold=1.0, tie_behavior="inconclusive"),
        "assumptions": [
            "Constructed scalar-addition calibration case; not an observed research defect"
        ],
    }
)


LSTSQ_CONTRACT = FIXTURE_CONTRACT.model_copy(update={
    "input_schema": InputSchema(fields=[InputField(name="matrix", dtype="float64", shape=[4, 2], min_value=-10, max_value=10)]),
    "conclusion": ConclusionRule(operator="gt", threshold=.75, tie_behavior="inconclusive"),
    "assumptions": ["Rank fraction > .75 declares both parameters identifiable under the specified numerical rank cutoff.",
                    "Historical documented lstsq default change; this input is constructed, not a downstream defect."],
    "review_provenance": "numpy_25721_lstsq_default",
})


def compare_searches(runner: RunnerAdapter, pairs_per_strategy: int = 8, seed: int = 42) -> dict:
    """Every strategy receives the same paired-execution cap and runs to the cap.

    Informed sweep deliberately receives knowledge of the boundary. This small
    constructed comparison is not evidence of general superiority.
    """
    if not 1 <= pairs_per_strategy <= 32:
        raise ValueError("pairs_per_strategy must be between 1 and 32")
    rows = []
    stopped = False
    failed = False
    for case, contract in (
        ("power_ratio", FIXTURE_CONTRACT),
        ("sensor_offset", SENSOR_CONTRACT),
        ("lstsq_rank", LSTSQ_CONTRACT),
        ("unchanged_control", FIXTURE_CONTRACT),
    ):
        for strategy in ("fixed_replay", "random_valid", "informed_sweep"):
            rng = random.Random(seed)
            records = []
            first_witness = None
            for i in range(pairs_per_strategy):
                if case == "unchanged_control":
                    signal = [0.5] * 4 + [1.5] * 4
                elif strategy == "random_valid":
                    signal = [rng.uniform(-10, 10) for _ in range(8)]
                elif strategy == "fixed_replay":
                    signal = [0.5] * 4 + [1.5] * 4 if i % 2 == 0 else [1.5] * 4 + [0.5] * 4
                else:
                    value = np.float32(1.0)
                    for _ in range(i + 1):
                        value = np.nextafter(value, np.float32(2.0))
                    signal = [1.0] * 7 + [float(value)] if case == "power_ratio" else [1.0] * 8
                candidate = {"signal": signal}
                if case == "lstsq_rank":
                    if strategy == "random_valid":
                        candidate = {"matrix": [[rng.uniform(-10, 10) for _ in range(2)] for _ in range(4)]}
                    else:
                        scale = (1.5 + (i % 4) * .5) * np.finfo(np.float64).eps if strategy == "informed_sweep" else .5
                        candidate = {"matrix": [[1., 0.], [0., float(scale)], [0., 0.], [0., 0.]]}
                digest = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()
                results = []
                for kind in ("old", "new"):
                    manifest = RunManifest(
                        run_id=make_run_id(),
                        kind=kind,
                        candidate_sha256=digest,
                        entry_point="run.py",
                        input_json=candidate,
                        case="power_ratio" if case == "unchanged_control" else case,
                    )
                    try:
                        result = runner.submit(manifest)
                        results.append(result.model_dump(mode="json"))
                    except BudgetExceeded as exc:
                        records.append(
                            {
                                "input": candidate,
                                "input_sha256": digest,
                                "runs": results,
                                "error": str(exc),
                            }
                        )
                        stopped = True
                        break
                if stopped:
                    break
                comparison = compare_outputs(results[0], results[1], contract)
                if comparison.get("verdict_hint") == VerdictKind.setup_failure:
                    failed = True
                if comparison.get("conclusion_changed") is True and first_witness is None:
                    first_witness = i + 1
                records.append(
                    {
                        "input": candidate,
                        "input_sha256": digest,
                        "runs": results,
                        "comparison": comparison,
                    }
                )
            rows.append(
                {
                    "case": case,
                    "strategy": strategy,
                    "seed": seed,
                    "pair_budget": pairs_per_strategy,
                    "completed_pairs": sum("comparison" in item and item["comparison"].get("verdict_hint") != VerdictKind.setup_failure for item in records),
                    "failed_pairs": sum(item.get("comparison", {}).get("verdict_hint") == VerdictKind.setup_failure for item in records),
                    "first_witness_pair": first_witness,
                    "contract_hash": hash_contract(contract),
                    "contract_body": contract.model_dump(mode="json"),
                    "records": records,
                }
            )
            if stopped:
                break
        if stopped:
            break
    return {
        "schema_version": "2",
        "historical_sources": [{"case": "lstsq_rank", "url": "https://github.com/numpy/numpy/pull/25721",
                                "commit": "80acab201eb5445af925552468983073616284dc",
                                "classification": "documented compatibility change; independently sourced mechanism, constructed input"}],
        "status": "budget_exhausted" if stopped else "execution_failed" if failed else "completed",
        "rows": rows,
        "limitations": [
            "Power and sensor inputs share the promotion mechanism; least-squares replays an independent documented default change. No case establishes a downstream scientific defect.",
            "Informed sweep uses the known boundary; fixed replay and seeded random search do not.",
            "Equal caps apply per strategy; incomplete rows cannot support a comparative claim.",
            "Witnesses are changed declared outcomes, not proof that either environment is wrong.",
        ],
    }
