"""Initial schema — all tables.

Revision ID: 0001
Revises:
Create Date: 2024-10-10 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── projects ──────────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    # ── artifacts ─────────────────────────────────────────────────────────────
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer, nullable=False),
        sa.Column("media_type", sa.Text, nullable=False, server_default="application/octet-stream"),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("owner_id", "sha256", name="uq_artifacts_owner_sha256"),
    )
    op.create_index("ix_artifacts_owner_id", "artifacts", ["owner_id"])

    # ── environments ──────────────────────────────────────────────────────────
    op.create_table(
        "environments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "manifest_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("python_version", sa.Text, nullable=True),
        sa.Column("platform", sa.Text, nullable=True),
        sa.Column("image_digest", sa.Text, nullable=True),
        sa.UniqueConstraint("owner_id", "manifest_sha256", name="uq_environments_owner_manifest"),
    )
    op.create_index("ix_environments_owner_id", "environments", ["owner_id"])

    # ── investigations (without active_contract_id FK — added after contracts) ─
    op.create_table(
        "investigations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column(
            "source_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column(
            "old_environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id"),
            nullable=True,
        ),
        sa.Column(
            "new_environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id"),
            nullable=True,
        ),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("budget_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("active_contract_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('draft','preflight','approved','running','verifying','completed','failed','cancelled')",
            name="ck_investigations_status",
        ),
    )
    op.create_index("ix_investigations_project_id", "investigations", ["project_id"])
    op.create_index(
        "investigations_project_created_idx",
        "investigations",
        ["project_id", sa.text("created_at DESC")],
    )

    # ── contracts ─────────────────────────────────────────────────────────────
    op.create_table(
        "contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("body_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="draft"),
        sa.Column("approved_by", sa.Text, nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "investigation_id", "version", name="uq_contracts_investigation_version"
        ),
    )
    op.create_index("ix_contracts_investigation_id", "contracts", ["investigation_id"])

    # Back-fill the FK from investigations → contracts.
    op.create_foreign_key(
        "fk_investigations_active_contract",
        "investigations",
        "contracts",
        ["active_contract_id"],
        ["id"],
        use_alter=True,
    )

    # ── candidates ────────────────────────────────────────────────────────────
    op.create_table(
        "candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id"),
            nullable=False,
        ),
        sa.Column(
            "input_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column("input_sha256", sa.String(64), nullable=True),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("validity", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_candidates_investigation_id", "candidates", ["investigation_id"])

    # ── runs ──────────────────────────────────────────────────────────────────
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id"),
            nullable=True,
        ),
        sa.Column(
            "environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id"),
            nullable=True,
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column(
            "output_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column(
            "trace_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column("resource_json", postgresql.JSONB, nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','timed_out','cancelled')",
            name="ck_runs_status",
        ),
    )
    op.create_index("ix_runs_investigation_id", "runs", ["investigation_id"])

    # ── proposals ─────────────────────────────────────────────────────────────
    op.create_table(
        "proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("model_id", sa.Text, nullable=True),
        sa.Column("prompt_template_version", sa.Text, nullable=True),
        sa.Column("request_digest", sa.String(64), nullable=True),
        sa.Column(
            "response_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column("validation_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_proposals_investigation_id", "proposals", ["investigation_id"])

    # ── interventions ─────────────────────────────────────────────────────────
    op.create_table(
        "interventions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id"),
            nullable=True,
        ),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("proposals.id"),
            nullable=True,
        ),
        sa.Column(
            "change_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column("predicted_effect_json", postgresql.JSONB, nullable=True),
        sa.Column("observed_effect_json", postgresql.JSONB, nullable=True),
        sa.Column("assessment", sa.Text, nullable=True),
    )
    op.create_index("ix_interventions_investigation_id", "interventions", ["investigation_id"])

    # ── holdout_sets ──────────────────────────────────────────────────────────
    op.create_table(
        "holdout_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id"),
            nullable=False,
        ),
        sa.Column(
            "manifest_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column(
            "sealed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_holdout_sets_investigation_id", "holdout_sets", ["investigation_id"])

    # ── patches ───────────────────────────────────────────────────────────────
    op.create_table(
        "patches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id"),
            nullable=False,
        ),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("proposals.id"),
            nullable=True,
        ),
        sa.Column(
            "diff_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column("diff_sha256", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_patches_investigation_id", "patches", ["investigation_id"])

    # ── verifications ─────────────────────────────────────────────────────────
    op.create_table(
        "verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "patch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patches.id"),
            nullable=False,
        ),
        sa.Column(
            "holdout_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("holdout_sets.id"),
            nullable=False,
        ),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column(
            "summary_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.Column("resource_json", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_verifications_patch_id", "verifications", ["patch_id"])

    # ── events ────────────────────────────────────────────────────────────────
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "investigation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("investigations.id"),
            nullable=False,
        ),
        sa.Column("sequence_no", sa.Integer, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("payload_json", postgresql.JSONB, nullable=True),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "investigation_id", "sequence_no", name="uq_events_investigation_seq"
        ),
    )
    op.create_index("ix_events_investigation_id", "events", ["investigation_id"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("verifications")
    op.drop_table("patches")
    op.drop_table("holdout_sets")
    op.drop_table("interventions")
    op.drop_table("proposals")
    op.drop_table("runs")
    op.drop_table("candidates")
    op.drop_constraint("fk_investigations_active_contract", "investigations", type_="foreignkey")
    op.drop_table("contracts")
    op.drop_table("investigations")
    op.drop_table("environments")
    op.drop_table("artifacts")
    op.drop_table("projects")
