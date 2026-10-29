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
        "declared_packages": ["numpy"],
    }


def test_only_exact_dependency_edit_is_eligible():
    eligible = check_eligibility(_profile(), _review())
    assert eligible["eligible"] and eligible["reason_codes"] == []
    extra_file = _review()
    extra_file["changed_paths"].append("src/model.py")
    extra_file["changed_file_details"].append({"path": "src/model.py", "status": "modified"})
    result = check_eligibility(_profile(), extra_file)
    assert result["mode"] == "diff_review_only"
    assert "application_files_changed" in result["reason_codes"]
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


def test_unresolved_include_cannot_be_admitted_as_numpy_only():
    from app.domain.dependency_review import build_review

    review = build_review(
        "github", "owner/repo", 1, "a" * 40, "b" * 40,
        [{"path": "requirements.txt", "old_path": None, "status": "modified", "patch": "pin"}],
        [{"path": "requirements.txt", "old_path": "requirements.txt",
          "old": "numpy==1.26.4\n",
          "new": "numpy==2.0.2\n-r hidden.txt\n"}], True,
    )
    assert review["complete"] is True
    assert len(review["dependency_changes"]) == 1
    assert "unresolved_manifest" in check_eligibility(_profile(), review)["reason_codes"]


def test_unparsed_index_directive_cannot_be_admitted():
    from app.domain.dependency_review import build_review

    review = build_review(
        "github", "owner/repo", 1, "a" * 40, "b" * 40,
        [{"path": "requirements.txt", "old_path": None, "status": "modified", "patch": "pin"}],
        [{"path": "requirements.txt", "old_path": "requirements.txt",
          "old": "numpy==1.26.4\n",
          "new": "numpy==2.0.2\n--extra-index-url https://example.invalid\n"}], True,
    )
    assert "unresolved_manifest" in check_eligibility(_profile(), review)["reason_codes"]


def test_unchanged_extra_dependency_is_not_claimed_as_installed():
    review = _review()
    review["declared_packages"] = ["numpy", "scipy"]
    assert "unsupported_dependency_set" in check_eligibility(_profile(), review)["reason_codes"]
