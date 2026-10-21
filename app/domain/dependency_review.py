"""Deterministic dependency observations from immutable manifest contents.

No source text is evaluated or sent to a model. Constraints are not called
resolved versions; unsupported formats and incomplete inputs remain explicit.
"""
from __future__ import annotations

import json
import re
import tomllib
from urllib.parse import quote


def is_manifest(path: str) -> bool:
    return bool(re.search(
        r"(^|/)(requirements[^/]*\.txt|pyproject\.toml|poetry\.lock|uv\.lock|Pipfile(?:\.lock)?|environment\.ya?ml)$",
        path, re.I,
    ))


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _requirement(value: str) -> tuple[str, str] | None:
    value = value.split(" #", 1)[0].strip()
    if not value or value.startswith(("#", "-")):
        return None
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*([^;]*)(?:;.*)?", value)
    if not match:
        return None
    return _name(match[1]), match[2].strip() or "unconstrained"


def parse_manifest(path: str, content: str | None) -> tuple[dict[str, str], list[str]]:
    """Return declared requirements/pins and explicit parsing limitations."""
    if content is None:
        return {}, []  # file does not exist at this commit
    name = path.rsplit("/", 1)[-1].lower()
    result: dict[str, str] = {}
    limits: list[str] = []

    def add(value):
        if isinstance(value, str) and ";" in value:
            limits.append("Environment-marked requirements are conditional and need resolution.")
        parsed = _requirement(value) if isinstance(value, str) else None
        if parsed:
            package, spec = parsed
            if package in result and result[package] != spec:
                limits.append(f"Multiple declarations for {package}; environment markers need resolution.")
                result[package] = " | ".join(sorted({result[package], spec}))
            else:
                result[package] = spec

    try:
        if name.endswith(".txt"):
            for line in content.splitlines():
                add(line)
                if line.strip().startswith(("-r", "--requirement", "-c", "--constraint", "-e")):
                    limits.append("Included requirements, constraints or editable packages are not resolved.")
        elif name in {"pyproject.toml", "poetry.lock", "uv.lock"}:
            data = tomllib.loads(content)
            if name == "pyproject.toml":
                project = data.get("project", {})
                for item in project.get("dependencies", []):
                    add(item)
                optional_groups = project.get("optional-dependencies", {})
                if optional_groups:
                    limits.append("Optional dependency groups are not guaranteed to be installed.")
                for group in optional_groups.values():
                    for item in group:
                        add(item)
                for package, spec in data.get("tool", {}).get("poetry", {}).get("dependencies", {}).items():
                    if package != "python":
                        if isinstance(spec, dict) and (spec.get("optional") or spec.get("markers")):
                            limits.append("Optional or environment-marked Poetry dependencies need resolution.")
                        result[_name(package)] = str(spec.get("version", "unresolved") if isinstance(spec, dict) else spec)
                if project.get("dynamic"):
                    limits.append("Dynamic project metadata is not resolved.")
            else:
                for item in data.get("package", []):
                    package, version = item.get("name"), item.get("version")
                    if isinstance(package, str) and isinstance(version, str):
                        add(f"{package}=={version}")
        elif name == "pipfile.lock":
            data = json.loads(content)
            for group in ("default", "develop"):
                for package, info in data.get(group, {}).items():
                    if isinstance(info, dict) and info.get("markers"):
                        limits.append("Environment-marked Pipfile dependencies need resolution.")
                    result[_name(package)] = info.get("version", "unresolved")
        elif name == "pipfile":
            data = tomllib.loads(content)
            for group in ("packages", "dev-packages"):
                for package, spec in data.get(group, {}).items():
                    if isinstance(spec, dict) and spec.get("markers"):
                        limits.append("Environment-marked Pipfile dependencies need resolution.")
                    result[_name(package)] = str(spec.get("version", "unresolved") if isinstance(spec, dict) else spec)
        else:
            # Read only simple conda/pip dependency-list entries, never YAML tags.
            for line in content.splitlines():
                match = re.match(r"^\s+-\s+([A-Za-z0-9_.-]+)\s*(==|>=|<=|~=|>|<|=)([^#]+)", line)
                if match:
                    add(match[1] + ("==" if match[2] == "=" else match[2]) + match[3].strip())
            limits.append("Conda YAML is a simple dependency-list scan; channels, builds and complex YAML are not resolved.")
    except (ValueError, TypeError, AttributeError) as exc:
        return {}, [f"Could not parse {path}: {type(exc).__name__}."]
    return result, sorted(set(limits))


def source_url(provider: str, repository: str, sha: str, path: str) -> str:
    middle = "blob" if provider == "github" else "-/blob"
    return f"https://{'github.com' if provider == 'github' else 'gitlab.com'}/{quote(repository, safe='/')}/{middle}/{sha}/{quote(path, safe='/')}"


def build_review(provider: str, repository: str, number: int, base_sha: str, head_sha: str,
                 files: list[dict], manifests: list[dict], complete: bool) -> dict:
    changes, limitations, evidence = [], [], []
    for manifest in manifests:
        if manifest.get("unavailable"):
            limitations.extend(manifest.get("limitations", []))
            continue
        old, old_limits = parse_manifest(manifest.get("old_path", manifest["path"]), manifest.get("old"))
        new, new_limits = parse_manifest(manifest["path"], manifest.get("new"))
        limitations.extend(old_limits + new_limits + manifest.get("limitations", []))
        for package in sorted(old.keys() | new.keys()):
            if old.get(package) == new.get(package):
                continue
            change = {"package": package, "before": old.get(package), "after": new.get(package),
                      "path": manifest["path"], "evidence_kind": "manifest declaration",
                      "before_url": source_url(provider, repository, base_sha, manifest.get("old_path", manifest["path"])),
                      "after_url": source_url(provider, repository, head_sha, manifest["path"])}
            changes.append(change)
            evidence.append({"title": f"{package}: {change['before'] or 'absent'} → {change['after'] or 'absent'}",
                             "evidence": manifest["path"], "url": change["after_url"],
                             "action": "Resolve both environments and verify this declaration against their installed versions."})
    if not complete:
        limitations.append("Changed-file retrieval reached its cap or the host omitted/truncated a diff; the review is incomplete.")
    limitations.append("Declared constraints do not establish resolved environments or a scientific regression.")
    if not manifests:
        limitations.append("No supported dependency manifest was available in the inspected files.")
    if not changes:
        evidence.append({"title": "No dependency version change established", "evidence": f"{len(files)} files inspected",
                         "action": "Inspect the missing information and full source diff; this is not a passing test result."})
    scientific = [f["path"] for f in files if re.search(r"\b(dtype|float32|float64|threshold|assert_allclose|isclose)\b", f.get("patch", ""))]
    if scientific:
        evidence.append({"title": "Numerical code or assertions need inspection", "evidence": ", ".join(scientific[:5]),
                         "url": source_url(provider, repository, head_sha, scientific[0]),
                         "action": "Identify the declared scientific output; keyword matches are a hypothesis, not causal evidence."})
    next_actions = ["Inspect the linked dependency declarations and resolve exact old/new environments.",
                    "Declare the scientific output and decision rule, including ties and tolerances."]
    if manifests:
        next_actions.append("Check paired-run support for the selected manifest; if unsupported, run the project's own tests in both environments.")
    else:
        next_actions.append("No runnable environment was established here. Run the project's own tests; add a validated adapter and pinned environments for a paired investigation.")
    return {"schema_version": "2", "mode": "metadata_review_only", "evidence_level": "Diff review",
            "executed_repository_code": False, "provider": provider, "repository": repository, "number": number,
            "base_sha": base_sha, "head_sha": head_sha, "complete": complete,
            "scope": f"{len(files)} changed files inspected at {head_sha[:12]}; no repository code executed.",
            "changed_file_count_checked": len(files), "dependency_files": [m["path"] for m in manifests],
            "changed_paths": [f["path"] for f in files],
            "changed_file_details": [{"path": f["path"], "old_path": f.get("old_path"),
                                      "status": f.get("status")} for f in files],
            "dependency_changes": changes, "numpy_mentioned": any("numpy" in f.get("patch", "").lower() for f in files),
            "findings": evidence, "limitations": sorted(set(limitations)),
            "next_actions": next_actions,
            "next_step": "Check the supported-run requirements before any contract is offered."}
