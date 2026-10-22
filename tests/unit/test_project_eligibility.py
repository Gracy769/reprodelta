from app.domain.project_eligibility import check_eligibility


def _profile():
    return {
        "id": "p1",
        "status": "approved",
        "contract_hash": "abc",
        "draft_json": {
            "provider": "github",
            "repository": "owner/repo",
            "dependency_path": "requirements.txt",
        },
        "approval_json": {"old": {"numpy_version": "1.26.4"}, "new": {"numpy_version": "2.0.2"}},
    }


def _review():
    return {
        "provider": "github",
        "repository": "owner/repo",
        "complete": True,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "changed_paths": ["requirements.txt"],
        "changed_file_details": [
            {"path": "requirements.txt", "old_path": None, "status": "modified"}
        ],
        "dependency_changes": [
            {
                "path": "requirements.txt",
                "package": "numpy",
                "before": "==1.26.4",
                "after": "==2.0.2",
            }
        ],
    }


def test_only_exact_dependency_edit_is_eligible():
    assert check_eligibility(_profile(), _review())["eligible"]
    extra_file = _review()
    extra_file["changed_paths"].append("src/model.py")
    extra_file["changed_file_details"].append({"path": "src/model.py", "status": "modified"})
    result = check_eligibility(_profile(), extra_file)
    assert result["mode"] == "diff_review_only"
    assert any("application files" in reason for reason in result["reasons"])


def test_incomplete_diff_and_pin_mismatch_are_rejected():
    review = _review()
    review["complete"] = False
    review["dependency_changes"][0]["after"] = ">=2"
    result = check_eligibility(_profile(), review)
    assert not result["eligible"]
    assert len(result["reasons"]) == 2


def test_second_dependency_change_in_same_file_is_diff_review_only():
    review = _review()
    review["dependency_changes"].append({
        "path": "requirements.txt", "package": "scipy",
        "before": "==1.13.1", "after": "==1.14.0",
    })
    result = check_eligibility(_profile(), review)
    assert result["mode"] == "diff_review_only"
    assert any("more than the one approved NumPy pin" in reason for reason in result["reasons"])
