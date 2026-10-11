"""SQLAlchemy ORM models for all Agent Replay Lab tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.storage.db import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_now
    )

    investigations: Mapped[list[Investigation]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("owner_id", "sha256", name="uq_artifacts_owner_sha256"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False, default="application/octet-stream")
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_now
    )


# ---------------------------------------------------------------------------
# environments
# ---------------------------------------------------------------------------


class Environment(Base):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("owner_id", "manifest_sha256", name="uq_environments_owner_manifest"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    manifest_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    python_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_digest: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# investigations
# ---------------------------------------------------------------------------

_INVESTIGATION_STATUSES = (
    "draft", "preflight", "approved", "running", "verifying",
    "completed", "failed", "cancelled",
)


class Investigation(Base):
    __tablename__ = "investigations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','preflight','approved','running','verifying','completed','failed','cancelled')",
            name="ck_investigations_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True
    )
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    old_environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id"), nullable=True
    )
    new_environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    budget_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    active_contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_now
    )

    project: Mapped[Project] = relationship(back_populates="investigations")
    contracts: Mapped[list[Contract]] = relationship(
        back_populates="investigation",
        foreign_keys="Contract.investigation_id",
        cascade="all, delete-orphan",
    )
    candidates: Mapped[list[Candidate]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )
    runs: Mapped[list[Run]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )
    interventions: Mapped[list[Intervention]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )
    patches: Mapped[list[Patch]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )
    holdout_sets: Mapped[list[HoldoutSet]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )
    events: Mapped[list[Event]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint("investigation_id", "version", name="uq_contracts_investigation_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    investigation: Mapped[Investigation] = relationship(
        back_populates="contracts", foreign_keys=[investigation_id]
    )


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), nullable=False, index=True
    )
    input_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    validity: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=True
    )

    investigation: Mapped[Investigation] = relationship(back_populates="candidates")
    runs: Mapped[list[Run]] = relationship(back_populates="candidate")


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

_RUN_STATUSES = ("queued", "running", "succeeded", "failed", "timed_out", "cancelled")


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','timed_out','cancelled')",
            name="ck_runs_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    output_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    trace_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    resource_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    investigation: Mapped[Investigation] = relationship(back_populates="runs")
    candidate: Mapped[Candidate] = relationship(back_populates="runs")


# ---------------------------------------------------------------------------
# proposals
# ---------------------------------------------------------------------------


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_template_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    validation_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_now
    )


# ---------------------------------------------------------------------------
# interventions
# ---------------------------------------------------------------------------


class Intervention(Base):
    __tablename__ = "interventions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id"), nullable=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposals.id"), nullable=True
    )
    change_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    predicted_effect_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    observed_effect_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    assessment: Mapped[str | None] = mapped_column(Text, nullable=True)

    investigation: Mapped[Investigation] = relationship(back_populates="interventions")


# ---------------------------------------------------------------------------
# holdout_sets
# ---------------------------------------------------------------------------


class HoldoutSet(Base):
    __tablename__ = "holdout_sets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), nullable=False, index=True
    )
    manifest_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sealed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, default=_now)

    investigation: Mapped[Investigation] = relationship(back_populates="holdout_sets")
    verifications: Mapped[list[Verification]] = relationship(back_populates="holdout_set")


# ---------------------------------------------------------------------------
# patches
# ---------------------------------------------------------------------------


class Patch(Base):
    __tablename__ = "patches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), nullable=False, index=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposals.id"), nullable=True
    )
    diff_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    diff_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_now
    )

    investigation: Mapped[Investigation] = relationship(back_populates="patches")
    verifications: Mapped[list[Verification]] = relationship(back_populates="patch")


# ---------------------------------------------------------------------------
# verifications
# ---------------------------------------------------------------------------


class Verification(Base):
    __tablename__ = "verifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    patch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patches.id"), nullable=False, index=True
    )
    holdout_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("holdout_sets.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    summary_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )
    resource_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_now
    )

    patch: Mapped[Patch] = relationship(back_populates="verifications")
    holdout_set: Mapped[HoldoutSet] = relationship(back_populates="verifications")


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("investigation_id", "sequence_no", name="uq_events_investigation_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id"), nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=_now
    )

    investigation: Mapped[Investigation] = relationship(back_populates="events")
