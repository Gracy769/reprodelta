"""Optional independent ratio references; agreement alone is compatibility."""

import hashlib
import json
import math

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RepairOracle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_by_input_sha256: dict[str, float] = Field(min_length=1, max_length=256)
    provenance: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def finite_references(self):
        for digest, value in self.reference_by_input_sha256.items():
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or not math.isfinite(value):
                raise ValueError("Reference requires a SHA-256 input digest and finite ratio")
        return self

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(), sort_keys=True).encode()).hexdigest()
