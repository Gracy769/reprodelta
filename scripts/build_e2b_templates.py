"""Prepare the two fixed NumPy pilot images without uploading repository source.

Run with --build only after verifying the E2B account and expected build cost.
The output template IDs and lock digests are operator inputs to profile approval.
"""

from __future__ import annotations

import argparse
import hashlib
import os

VERSIONS = ("1.26.4", "2.0.2")


def lock_bytes(version: str) -> bytes:
    if version not in VERSIONS:
        raise ValueError("Only the two reviewed NumPy versions are supported")
    return f"python==3.12\nnumpy=={version}\n".encode("ascii")


def build(version: str, prefix: str) -> tuple[str, str]:
    from e2b import Template

    marker = lock_bytes(version)
    digest = hashlib.sha256(marker).hexdigest()
    alias = f"{prefix}-numpy-{version.replace('.', '')}"
    # The build has internet only to install this one exact reviewed package.
    # Source snapshots are uploaded later to runtime sandboxes with internet off.
    template = (
        Template()
        .from_python_image("3.12")
        .pip_install([f"numpy=={version}"])
        .run_cmd(
            "printf 'python==3.12\\nnumpy=="
            + version
            + "\\n' > /home/user/reprodelta-environment.lock"
        )
    )
    info = Template.build(template, alias=alias)
    return str(info.template_id), digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Create billable E2B templates")
    parser.add_argument("--name-prefix", default="reprodelta-pilot")
    args = parser.parse_args()
    if not args.name_prefix.replace("-", "").isalnum() or len(args.name_prefix) > 40:
        parser.error("Name prefix must use up to 40 letters, digits and hyphens")
    for version in VERSIONS:
        digest = hashlib.sha256(lock_bytes(version)).hexdigest()
        print(f"NumPy {version}: lock_sha256={digest}")
    if not args.build:
        print("Dry run only. Pass --build after reviewing E2B usage cost.")
        return
    if not os.environ.get("E2B_API_KEY"):
        parser.error("E2B_API_KEY is required for --build")
    for version in VERSIONS:
        template_id, digest = build(version, args.name_prefix)
        print(f"NumPy {version}: template_id={template_id} lock_sha256={digest}")


if __name__ == "__main__":
    main()
