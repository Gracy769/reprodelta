"""Strict, dependency-free patch application for the curated numerical fixture.

This is a deliberately restricted numerical language, not a Python sandbox.
Workers independently validate the source before executing it in a child process.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

PROJECT_FILES = ("pipeline.py", "run.py", "sensor.py", "least_squares.py")
PATCH_PATH = "fixture/project/pipeline.py"


def project_sources(root: Path) -> dict[str, str]:
    if any((root / name).is_symlink() for name in PROJECT_FILES):
        raise ValueError("Symlink project files are forbidden")
    return {name: (root / name).read_text(encoding="utf-8") for name in PROJECT_FILES}


def project_digest(sources: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()


def apply_diff(source: str, diff: str) -> str:
    """Apply exact unified hunks; reject offsets, fuzzy matching and extra data."""
    if len(diff) > 100000:
        raise ValueError("Patch exceeds size limit")
    lines = diff.splitlines()
    if lines and lines[0] == f"diff --git a/{PATCH_PATH} b/{PATCH_PATH}":
        lines.pop(0)
        if lines and re.fullmatch("index [0-9a-f]+\\.\\.[0-9a-f]+(?: 100644)?", lines[0]):
            lines.pop(0)
    if lines[:2] != [f"--- a/{PATCH_PATH}", f"+++ b/{PATCH_PATH}"]:
        raise ValueError("Expected exactly one allowlisted source diff")
    original = source.splitlines()
    output: list[str] = []
    cursor = 0
    i = 2
    hunks = 0
    while i < len(lines):
        match = re.fullmatch("@@ -(\\d+)(?:,(\\d+))? \\+(\\d+)(?:,(\\d+))? @@.*", lines[i])
        if not match:
            raise ValueError("Malformed hunk or trailing patch data")
        old_start, old_count, new_start, new_count = (
            int(match[1]),
            int(match[2] or 1),
            int(match[3]),
            int(match[4] or 1),
        )
        start = old_start - 1 if old_count else old_start
        if start < cursor or start > len(original):
            raise ValueError("Overlapping or out-of-range hunk")
        output.extend(original[cursor:start])
        if (new_start - 1 if new_count else new_start) != len(output):
            raise ValueError("Incorrect new hunk position")
        cursor = start
        removed = added = 0
        i += 1
        while i < len(lines) and (not lines[i].startswith("@@ ")):
            line = lines[i]
            if not line or line[0] not in " +-":
                raise ValueError("Invalid hunk line")
            if line[0] in " -":
                if cursor >= len(original) or original[cursor] != line[1:]:
                    raise ValueError("Patch context does not match immutable source")
                cursor += 1
                removed += 1
            if line[0] in " +":
                output.append(line[1:])
                added += 1
            i += 1
        if (removed, added) != (old_count, new_count):
            raise ValueError("Hunk counts do not match")
        hunks += 1
    if not hunks:
        raise ValueError("No patch hunks")
    output.extend(original[cursor:])
    patched = "\n".join(output) + "\n"
    validate_numerical_source(source, patched)
    return patched


def validate_numerical_source(original: str, patched: str) -> None:
    """Permit bounded arithmetic edits inside existing fixture functions only."""
    before, after = (ast.parse(original), ast.parse(patched))
    if len(before.body) != len(after.body):
        raise ValueError("Module structure must remain unchanged")
    calls = {"float", "len", "abs", "str"}
    numpy_calls = {"array", "float32", "float64", "sum"}
    allowed = (
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
        ast.Assign,
        ast.Return,
        ast.If,
        ast.Expr,
        ast.Constant,
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Attribute,
        ast.Call,
        ast.keyword,
        ast.ListComp,
        ast.comprehension,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.Subscript,
        ast.Slice,
        ast.Dict,
        ast.List,
        ast.Tuple,
        ast.IfExp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.Pow,
        ast.FloorDiv,
        ast.Mod,
        ast.USub,
        ast.UAdd,
        ast.Not,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    )
    for old, new in zip(before.body, after.body, strict=True):
        if not isinstance(old, ast.FunctionDef):
            if ast.dump(old) != ast.dump(new):
                raise ValueError("Imports, constants and module code are immutable")
            continue
        if not isinstance(new, ast.FunctionDef) or old.name != new.name:
            raise ValueError("Function definitions are immutable")
        old_header, new_header = (ast.dump(old.args), ast.dump(new.args))
        if (
            old_header != new_header
            or new.decorator_list
            or ast.dump(old.returns) != ast.dump(new.returns)
        ):
            raise ValueError("Function signatures are immutable")
        local_names = {
            node.id
            for node in ast.walk(new)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        if local_names & (calls | {"np", "THRESHOLD", "classify_signal", "compute_power_ratio"}):
            raise ValueError("Reserved names cannot be rebound")
        names = local_names | calls | {"np", "THRESHOLD", "signal", "compute_power_ratio", "dict"}
        for node in ast.walk(new):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                raise ValueError("Attribute mutation is forbidden")
            if not isinstance(node, allowed):
                raise ValueError(f"Unsupported numerical syntax: {type(node).__name__}")
            if isinstance(node, ast.Name) and node.id not in names:
                raise ValueError("Unknown numerical name")
            if isinstance(node, ast.Attribute) and (
                not isinstance(node.value, ast.Name)
                or not (
                    node.value.id == "np"
                    and node.attr in numpy_calls | {"ndarray", "__version__"}
                    or (node.value.id in local_names and node.attr == "dtype")
                )
            ):
                raise ValueError("Only allowlisted NumPy attributes are permitted")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id not in calls | {"compute_power_ratio"} or (
                        node.func.id == "compute_power_ratio" and new.name != "classify_signal"
                    ):
                        raise ValueError("Unsupported function call")
                elif not isinstance(node.func, ast.Attribute) or node.func.attr not in numpy_calls:
                    raise ValueError("Unsupported NumPy call")
            if isinstance(node, ast.comprehension) and (
                node.is_async
                or node.ifs
                or (not isinstance(node.iter, ast.Name))
                or (node.iter.id != "signal")
            ):
                raise ValueError("Comprehensions must iterate only over the bounded signal")
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                raise ValueError("Input mutation is forbidden")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and (abs(node.value) > 1000000)
            ):
                raise ValueError("Oversized numerical literal")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and (len(node.value) > 1000)
            ):
                raise ValueError("Oversized string literal")
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Mult)
                and (
                    isinstance(node.left, (ast.List, ast.Tuple, ast.Dict))
                    or isinstance(node.right, (ast.List, ast.Tuple, ast.Dict))
                )
            ):
                raise ValueError("Sequence multiplication is forbidden")
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow)) and (
                not isinstance(node.right, ast.Constant)
                or not isinstance(node.right.value, (int, float))
                or abs(node.right.value) > 8
            ):
                raise ValueError("Only small constant powers are permitted")
