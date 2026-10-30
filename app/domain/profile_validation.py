"""Immutable operator request to validate one draft against two prepared VMs."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.project_profile import PreparedEnvironment


class ProfileValidationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: uuid.UUID
    sandbox_provider: Literal["e2b", "oracle_docker"] = "e2b"
    provider: Literal["github", "gitlab"]
    repository: str = Field(min_length=3, max_length=255)
    source_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    old: PreparedEnvironment
    new: PreparedEnvironment
    max_wall_seconds: int = Field(default=300, ge=1, le=900)
    max_sandbox_seconds: int = Field(default=600, ge=1, le=1800)
    sandbox_usd_per_second: float = Field(gt=0)
    max_cost_usd: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def reviewed_boundary(self):
        if self.old.numpy_version != "1.26.4" or self.new.numpy_version != "2.0.2":
            raise ValueError("Pilot validation requires the reviewed NumPy pair")
        if not math.isfinite(self.sandbox_usd_per_second) or not math.isfinite(self.max_cost_usd):
            raise ValueError("Validation cost values must be finite")
        if self.max_sandbox_seconds * self.sandbox_usd_per_second > self.max_cost_usd:
            raise ValueError("Validation seconds exceed the cost ceiling")
        segments = self.repository.split("/")
        if len(segments) < 2 or (self.provider == "github" and len(segments) != 2):
            raise ValueError("Repository path is invalid")
        if any(segment in {"", ".", ".."} for segment in segments):
            raise ValueError("Repository path is invalid")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
