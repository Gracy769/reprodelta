"""Prepare the two fixed NumPy pilot images without uploading repository source.

Run with --build only after verifying the E2B account and expected build cost.
The output template IDs and lock digests are operator inputs to profile approval.
"""

from __future__ import annotations

import argparse
import os
import re

VERSIONS = ("1.26.4", "2.0.2")


def lock_command(version: str) -> str:
    if version not in VERSIONS:
        raise ValueError("Only the two reviewed NumPy versions are supported")
    return ("set -eu; packages=$(python -m pip freeze --all); "
            "{ printf 'reprodelta-lock-v2\\n'; python -V 2>&1; "
            "printf '%s\\n' \"$packages\" | LC_ALL=C sort; } "
            "> /home/user/reprodelta-environment.lock")


def build(version: str, prefix: str) -> tuple[str, str]:
    from e2b import Sandbox, Template

    alias = f"{prefix}-numpy-{version.replace('.', '')}"
    # The build has internet only to install this one exact reviewed package.
    # Source snapshots are uploaded later to runtime sandboxes with internet off.
    template = (
        Template()
        .from_python_image("3.12")
        .pip_install([f"numpy=={version}"])
        .run_cmd(lock_command(version))
    )
    info = Template.build(template, alias=alias)
    sandbox = Sandbox.create(template=str(info.template_id), timeout=60, secure=True,
                             allow_internet_access=False, envs={})
    try:
        observed = sandbox.commands.run(
            "sha256sum /home/user/reprodelta-environment.lock", timeout=15
        )
        if (observed.exit_code != 0 or not observed.stdout.split()
                or not re.fullmatch(r"[0-9a-f]{64}", observed.stdout.split()[0])):
            raise RuntimeError("Built template did not expose its environment fingerprint")
        digest = observed.stdout.split()[0]
    finally:
        sandbox.kill()
    return str(info.template_id), digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Create billable E2B templates")
    parser.add_argument("--name-prefix", default="reprodelta-pilot")
    args = parser.parse_args()
    if not args.name_prefix.replace("-", "").isalnum() or len(args.name_prefix) > 40:
        parser.error("Name prefix must use up to 40 letters, digits and hyphens")
    if not args.build:
        print("Dry run only. A resolved environment digest is available after building each template.")
        return
    if not os.environ.get("E2B_API_KEY"):
        parser.error("E2B_API_KEY is required for --build")
    for version in VERSIONS:
        template_id, digest = build(version, args.name_prefix)
        print(f"NumPy {version}: template_id={template_id} lock_sha256={digest}")


if __name__ == "__main__":
    main()
