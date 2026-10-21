"""Immutable admission record for a bounded approved-project investigation."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectRunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: uuid.UUID
    provider: Literal["github", "gitlab"]
    repository: str = Field(min_length=3, max_length=255)
    change_number: int = Field(ge=1, le=10_000_000)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    head_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    old_environment_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    new_environment_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    max_wall_seconds: int = Field(default=900, ge=1, le=900)
    max_sandbox_seconds: int = Field(default=1800, ge=1, le=1800)

    @model_validator(mode="after")
    def distinct_revisions(self):
        if self.base_sha == self.head_sha:
            raise ValueError("A change must have distinct base and head commits")
        if self.provider == "github" and len(self.repository.split("/")) != 2:
            raise ValueError("GitHub repositories must use owner/name")
        if any(part in {"", ".", ".."} for part in self.repository.split("/")):
            raise ValueError("Repository path is invalid")
        return self

    @property
    def digest(self) -> str:
        data = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
