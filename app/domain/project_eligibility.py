"""Conservative admission for a dependency-only approved-project run."""

from __future__ import annotations


def check_eligibility(profile: dict, review: dict) -> dict:
    """Explain why a reviewed PR can or cannot use an approved profile.

    This does not launch execution. The caller must recheck the PR revision and
    immutable source snapshot when the owner approves a run.
    """
    reasons: list[str] = []
    reason_codes: list[str] = []

    def reject(code: str, message: str) -> None:
        reason_codes.append(code)
        reasons.append(message)

    draft = profile.get("draft_json") or {}
    approval = profile.get("approval_json") or {}
    if profile.get("status") != "approved" or not approval:
        reject("profile_unapproved", "This project profile has not been approved for execution.")
    if review.get("provider") != draft.get("provider") or review.get("repository") != draft.get(
        "repository"
    ):
        reject("repository_mismatch", "The change belongs to a different source repository.")
    if not review.get("complete"):
        reject("incomplete_diff", "The source host did not return a complete change diff.")
    if review.get("manifest_resolution_limits"):
        reject("unresolved_manifest", "The dependency declaration contains unresolved requirements or markers.")
    if review.get("declared_packages") != ["numpy"]:
        reject("unsupported_dependency_set", "This pilot installs only NumPy; other declared packages need a separately approved environment.")
    paths = review.get("changed_paths")
    dependency_path = draft.get("dependency_path")
    if not isinstance(paths, list) or not paths:
        reject("missing_changed_files", "The changed-file list is unavailable.")
    elif any(path != dependency_path for path in paths):
        reject("application_files_changed",
            "The PR changes application files as well as the approved dependency declaration."
        )
    details = review.get("changed_file_details")
    if (
        not isinstance(details, list)
        or len(details) != 1
        or details[0].get("path") != dependency_path
        or details[0].get("old_path") not in {None, dependency_path}
        or details[0].get("status") not in {"modified", "changed"}
    ):
        reject("dependency_edit_not_isolated",
               "Only an in-place edit to the approved dependency file can be isolated.")
    expected_old = (approval.get("old") or {}).get("numpy_version")
    expected_new = (approval.get("new") or {}).get("numpy_version")
    changes = review.get("dependency_changes")
    if not isinstance(changes, list):
        changes = []
    matched = [
        change
        for change in changes
        if isinstance(change, dict)
        and change.get("path") == dependency_path
        and change.get("package") == "numpy"
        and change.get("before") == f"=={expected_old}"
        and change.get("after") == f"=={expected_new}"
    ]
    if len(matched) != 1:
        reject("approved_pins_missing",
               "The exact approved NumPy before/after pins were not established.")
    if len(changes) > 1 or any(not isinstance(change, dict) for change in changes):
        reject("multiple_dependency_changes",
               "The dependency file changes more than the one approved NumPy pin.")
    if not review.get("base_sha") or not review.get("head_sha"):
        reject("missing_revision", "Immutable base and head commits are missing.")
    return {
        "eligible": not reasons,
        "mode": "approved_project_run" if not reasons else "diff_review_only",
        "reasons": reasons,
        "reason_codes": reason_codes,
        "base_sha": review.get("base_sha"),
        "head_sha": review.get("head_sha"),
        "profile_id": profile.get("id"),
        "contract_hash": profile.get("contract_hash"),
    }
