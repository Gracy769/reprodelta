"""Offline equal-budget benchmark of one independently reported NumPy expression.

This is evaluation tooling, not an approved-project investigation or a claim
that the full upstream package was executed.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable

from app.domain.comparison import evaluate_output
from app.domain.project_candidates import candidate_inputs, feedback_candidates
from app.domain.project_profile import ProjectProfileDraft
from app.domain.validator import validate_input

FIXED_INPUTS = [
    {"reference": 0, "secondary": 0},
    {"reference": 0, "secondary": 1},
    {"reference": 1, "secondary": 0},
    {"reference": 3, "secondary": 1},
]


def evaluate_mask_searches(
    profile: ProjectProfileDraft,
    run_batch: Callable[[str, str, list[dict]], dict],
    *,
    seeds: list[int],
    pair_budget: int = 4,
) -> dict:
    """Run every strategy to the same pair cap, including unchanged controls.

    `run_batch(version, mode, inputs)` must run twice per input and return a
    version plus a list of two integer measurements per candidate.
    """
    if not 1 <= pair_budget <= len(FIXED_INPUTS):
        raise ValueError("Pair budget must be within 1–4")
    if not 1 <= len(seeds) <= 20 or len(set(seeds)) != len(seeds) or any(
        type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds
    ):
        raise ValueError("Use 1–20 distinct unsigned 32-bit seeds")
    if (profile.adapter_module != "mask_probe" or profile.adapter_function != "measure"
            or profile.contract.output_selector.field_name != "packed_mask"
            or profile.contract.conclusion.operator != "gt"
            or profile.contract.conclusion.threshold != 65535):
        raise ValueError("Evaluation requires the reviewed mask profile")
    contract = profile.contract
    neutral = {"reference": 0, "secondary": 0}
    if not validate_input(contract, neutral).valid:
        raise ValueError("Neutral control violates the profile contract")

    rows = []
    for seed in seeds:
        for case, mode in (("reported_expression", "original"),
                           ("unchanged_cast_control", "repaired")):
            for strategy in ("fixed", "sweep", "seeded_random", "adaptive"):
                began = time.monotonic()
                if strategy == "fixed":
                    candidates = FIXED_INPUTS[:pair_budget]
                elif strategy == "adaptive":
                    candidates = candidate_inputs(contract, neutral, strategy="sweep",
                                                  budget=max(1, (pair_budget + 1) // 2))
                else:
                    candidates = candidate_inputs(contract, neutral, strategy=strategy,
                                                  budget=pair_budget, seed=seed)
                records = []

                def run_phase(phase: list[dict], phase_mode: str,
                              phase_records: list[dict]) -> None:
                    for candidate in phase:
                        if not validate_input(contract, candidate).valid:
                            raise ValueError("Generated candidate violates the contract")
                    old = run_batch("1.26.4", phase_mode, phase)
                    new = run_batch("2.0.2", phase_mode, phase)
                    if (old.get("numpy_version") != "1.26.4"
                            or new.get("numpy_version") != "2.0.2"):
                        raise RuntimeError("Pinned NumPy version mismatch")
                    for index, candidate in enumerate(phase):
                        before, after = old["values"][index], new["values"][index]
                        if any(len(values) != 2 or any(type(value) is not int for value in values)
                               for values in (before, after)):
                            raise RuntimeError("Invalid repeated measurement")
                        stable = before[0] == before[1] and after[0] == after[1]
                        before_conclusion = evaluate_output(
                            {"packed_mask": before[0]}, contract
                        )["conclusion"]
                        after_conclusion = evaluate_output(
                            {"packed_mask": after[0]}, contract
                        )["conclusion"]
                        phase_records.append({
                            "input": candidate,
                            "old": before,
                            "new": after,
                            "repeatable": stable,
                            "changed_conclusion": stable and
                                before_conclusion != after_conclusion,
                        })

                run_phase(candidates, mode, records)
                if strategy == "adaptive" and len(records) < pair_budget:
                    if not all(item["repeatable"] for item in records):
                        raise RuntimeError("Adaptive feedback is not repeatable")
                    feedback = [(item["input"], item["old"][0], item["new"][0])
                                for item in records]
                    proposed = feedback_candidates(contract, neutral, feedback,
                                                   budget=pair_budget - len(records), seed=seed)
                    if len(proposed) != pair_budget - len(records):
                        raise RuntimeError("Adaptive search exhausted valid unique candidates")
                    run_phase(proposed, mode, records)
                if len(records) != pair_budget:
                    raise RuntimeError("Strategy did not complete its equal pair budget")
                if not all(item["repeatable"] for item in records):
                    raise RuntimeError("Benchmark observation is not repeatable")
                first = next((i + 1 for i, item in enumerate(records)
                              if item["changed_conclusion"]), None)
                rows.append({
                    "case": case, "strategy": strategy, "seed": seed,
                    "pair_budget": pair_budget, "completed_pairs": len(records),
                    "native_executions": len(records) * 4,
                    "first_witness_pair": first,
                    "false_alarms": int(first is not None) if case == "unchanged_cast_control" else None,
                    "unstable_pairs": sum(not item["repeatable"] for item in records),
                    "elapsed_seconds": round(time.monotonic() - began, 6),
                    "records": records,
                })
    protocol = {
        "source": "isce3 issue 335 scalar-shift expression, not the full package",
        "profile_sha256": profile.digest,
        "seeds": seeds, "pair_budget": pair_budget,
        "fixed_inputs": FIXED_INPUTS[:pair_budget],
        "initial_input": neutral,
        "repeats_per_environment": 2,
        "cases": ["reported_expression", "unchanged_cast_control"],
        "strategies": ["fixed", "sweep", "seeded_random", "adaptive"],
        "search_policy": "Complete all pairs even after first witness",
    }
    return {
        "schema_version": "1", "status": "completed",
        "protocol": protocol,
        "protocol_sha256": hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest(),
        "rows": rows,
        "totals": {
            "completed_pairs": sum(row["completed_pairs"] for row in rows),
            "native_executions": sum(row["native_executions"] for row in rows),
            "false_alarms": sum(row["false_alarms"] or 0 for row in rows),
        },
        "limitations": [
            "Only the reported scalar-shift expression runs, not the full isce3 package.",
            "The defect and repair were known before this benchmark; it is not blind discovery.",
            "The fixed input list includes a known witness in fourth position.",
            "Native local interpreter runs do not measure sandbox or model cost.",
            "A changed declared threshold outcome is not an independently reviewed scientific impact.",
        ],
    }
