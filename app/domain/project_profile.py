"""Versioned, owner-reviewed configuration for one scientific Python workflow."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.contract import ContractBody, hash_contract
from app.domain.dependency_review import is_manifest
from app.domain.validator import validate_input
from app.providers.e2b_runner import ProjectAdapter


class ProjectProfileDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["github", "gitlab"]
    repository: str = Field(
        min_length=3, max_length=255, pattern=r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$"
    )
    name: str = Field(min_length=3, max_length=100)
    adapter_module: str = Field(min_length=1, max_length=160)
    adapter_function: str = Field(min_length=1, max_length=100)
    dependency_path: str = Field(min_length=1, max_length=512)
    sample_input: dict
    contract: ContractBody
    source_transfer_consent: bool = False

    @model_validator(mode="after")
    def validate_profile(self):
        ProjectAdapter(self.adapter_module, self.adapter_function).validate()
        segments = self.repository.split("/")
        if self.provider == "github" and len(segments) != 2:
            raise ValueError("GitHub repositories must use owner/name")
        if any(segment in {".", ".."} for segment in segments):
            raise ValueError("Repository path contains an invalid segment")
        if (
            self.dependency_path.startswith("/")
            or "\\" in self.dependency_path
            or any(part in {"", ".", ".."} for part in self.dependency_path.split("/"))
        ):
            raise ValueError("Dependency path must be a safe relative path")
        if not is_manifest(self.dependency_path):
            raise ValueError("Dependency path must be a supported Python manifest")
        if self.contract.output_selector.field_name == "conclusion":
            raise ValueError("Project profiles must select a numeric output field")
        if not (1 <= len(self.contract.input_schema.fields) <= 8):
            raise ValueError("Pilot profiles require 1–8 numeric input fields")
        names = [field.name for field in self.contract.input_schema.fields]
        if len(names) != len(set(names)) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) for name in names
        ):
            raise ValueError("Pilot input fields need unique Python-style names")
        if set(self.sample_input) != set(names):
            raise ValueError("Sample input must contain exactly the declared fields")
        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]{0,63}", self.contract.output_selector.field_name
        ):
            raise ValueError("Numeric output field must have a Python-style name")
        for field in self.contract.input_schema.fields:
            if field.dtype not in {"float32", "float64", "int32", "int64"} or len(field.shape) > 1:
                raise ValueError(
                    "Pilot inputs support numeric scalars and one-dimensional arrays only"
                )
            if field.shape and field.shape[0] > 1000:
                raise ValueError("Pilot arrays are limited to 1000 elements")
            if any(
                value is not None and not math.isfinite(value)
                for value in (field.min_value, field.max_value)
            ) or (
                field.min_value is not None and field.max_value is not None
                and field.min_value > field.max_value
            ):
                raise ValueError("Input bounds must be finite and ordered")
        if not math.isfinite(self.contract.conclusion.threshold):
            raise ValueError("Decision threshold must be finite")
        comparison = self.contract.comparison
        if any(
            not math.isfinite(value) or value < 0
            for value in (comparison.atol, comparison.rtol)
        ):
            raise ValueError("Comparison tolerances must be finite and non-negative")
        if len(json.dumps(self.sample_input, allow_nan=False)) > 128_000:
            raise ValueError("Sample input exceeds 128 KB")
        result = validate_input(self.contract, self.sample_input)
        if not result.valid:
            raise ValueError(f"Sample input violates the contract: {result.reason}")
        return self

    @property
    def digest(self) -> str:
        body = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @property
    def contract_hash(self) -> str:
        return hash_contract(self.contract)


class PreparedEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    python_version: str = Field(pattern=r"^3\.12(?:\.\d+)?$")
    numpy_version: str = Field(pattern=r"^\d+(?:\.\d+){1,3}$")
    lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProfileApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_job_id: uuid.UUID
    old: PreparedEnvironment
    new: PreparedEnvironment
    review_note: str = Field(min_length=8, max_length=1000)
