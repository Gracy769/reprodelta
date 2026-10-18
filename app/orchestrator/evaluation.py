"""Multi-seed evaluation with explicit provenance and raw denominators."""

import hashlib
import json

from app.domain.contract import FIXTURE_CONTRACT
from app.domain.types import BudgetConfig
from app.orchestrator.benchmark import compare_searches
from app.orchestrator.budget import BudgetLedger, BudgetRunner
from app.orchestrator.search import SearchController

CASE_PROVENANCE = {
    "power_ratio": {"classification": "constructed", "scientific_ground_truth": "unreviewed",
                    "source": "https://numpy.org/doc/2.0/numpy_2_0_migration_guide.html"},
    "sensor_offset": {"classification": "constructed", "scientific_ground_truth": "unreviewed",
                      "source": "https://numpy.org/doc/2.0/numpy_2_0_migration_guide.html"},
    "lstsq_rank": {"classification": "documented_mechanism_constructed_input",
                   "scientific_ground_truth": "unreviewed",
                   "source": "https://github.com/numpy/numpy/pull/25721"},
    "unchanged_control": {"classification": "constructed_negative_control",
                          "scientific_ground_truth": "unchanged_by_construction"},
}


def evaluate_searches(runner, seeds=(13, 42, 97), pairs=4, max_runs=1000):
    if not 1 <= len(seeds) <= 20 or len(set(seeds)) != len(seeds):
        raise ValueError("Use 1 to 20 distinct seeds")
    if any(type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds):
        raise ValueError("Seeds must be unsigned 32-bit integers")
    if not 1 <= pairs <= 32 or not 2 <= max_runs <= 50_000:
        raise ValueError("Evaluation budgets are out of bounds")
    budget = BudgetConfig(max_candidates=1, max_runs=max_runs, max_wall_seconds=3600)
    ledger = BudgetLedger(budget)
    budgeted = BudgetRunner(runner, ledger)
    rows, statuses = [], []
    for seed in seeds:
        comparison = compare_searches(budgeted, pairs, seed)
        rows.extend(comparison["rows"])
        statuses.append(comparison["status"])
        if comparison["status"] != "completed":
            break
        # Opt-in adaptive algorithm is evaluated against the same cap on power
        # ratio only. Search stops at first witness; unused budget is not credit.
        search = SearchController(FIXTURE_CONTRACT,
                                  BudgetConfig(max_candidates=pairs, max_runs=max_runs), budgeted)
        progress = search.run_search("evaluation", seed, "adaptive")
        executed = [item for item in progress.candidates if item.pair_result is not None]
        rows.append({"case": "power_ratio", "strategy": "adaptive", "seed": seed,
                     "pair_budget": pairs, "completed_pairs": sum(item.pair_result.old_run.status == "ok"
                         and item.pair_result.new_run.status == "ok"
                         and item.pair_result.comparison.get("old_ratio") is not None for item in executed),
                     "first_witness_pair": next((i + 1 for i, item in enumerate(executed)
                                                 if item.pair_result.conclusion_changed), None),
                     "attempted_candidates": progress.attempted, "invalid_candidates": progress.invalid,
                     "status": progress.status,
                     "records": [item.model_dump(mode="json") for item in progress.candidates]})
        if progress.status == "failed" or progress.stop_reason and progress.stop_reason != "candidate_budget":
            statuses.append("incomplete")
            break
    metrics = []
    for case, strategy in sorted({(row["case"], row["strategy"]) for row in rows}):
        selected = [row for row in rows if row["case"] == case and row["strategy"] == strategy]
        first = [row["first_witness_pair"] for row in selected if row["first_witness_pair"] is not None]
        metrics.append({"case": case, "strategy": strategy, "attempted_seeds": len(selected),
                        "planned_seeds": len(seeds), "seeds_with_witness": len(first),
                        "completed_pairs": sum(row["completed_pairs"] for row in selected),
                        "mean_pairs_to_witness_when_found": sum(first) / len(first) if first else None,
                        "unchanged_control_false_alarms": len(first) if case == "unchanged_control" else None})
    protocol = {"version": "1", "seeds": list(seeds), "pair_cap": pairs, "max_runs": max_runs,
                "max_wall_seconds": 3600, "adaptive_algorithm": "feedback-boundary-v1", "cases": CASE_PROVENANCE}
    return {"schema_version": "1", "status": "completed" if all(s == "completed" for s in statuses) else "incomplete",
            "protocol": protocol, "protocol_sha256": hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest(),
            "metrics": metrics, "rows": rows, "usage": ledger.usage,
            "limitations": ["No independently confirmed downstream scientific defect is included.",
                            "Informed sweep knows fixture boundaries; adaptive search does not.",
                            "Adaptive search is evaluated only on power ratio and stops at the first witness.",
                            "Equal execution caps are not equal model/compute costs or proof of superiority.",
                            "Ground-truth labels are provenance, not model-generated scientific judgments."]}
