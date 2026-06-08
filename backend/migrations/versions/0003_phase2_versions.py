"""add phase2 versioned workspace tables

Revision ID: 0003_phase2_versions
Revises: 0002_phase1_workspace
Create Date: 2026-06-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_phase2_versions"
down_revision = "0002_phase1_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    version_status = sa.Enum(
        "queued",
        "running",
        "completed",
        "failed",
        name="decision_version_status",
    )
    op.create_table(
        "decision_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("status", version_status, nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=True),
        sa.Column("change_summary", sa.JSON(), nullable=False),
        sa.Column("gap_analysis", sa.JSON(), nullable=False),
        sa.Column("adr", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["decision_versions.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "session_id",
            "version_number",
            name="uq_decision_versions_session_version_number",
        ),
    )

    with op.batch_alter_table("decision_candidates") as batch_op:
        batch_op.add_column(sa.Column("decision_version_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "selection_state",
                sa.String(length=40),
                nullable=False,
                server_default="included",
            )
        )
        batch_op.add_column(
            sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_foreign_key(
            "fk_decision_candidates_decision_version_id",
            "decision_versions",
            ["decision_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("decision_criteria") as batch_op:
        batch_op.add_column(sa.Column("decision_version_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_foreign_key(
            "fk_decision_criteria_decision_version_id",
            "decision_versions",
            ["decision_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("evidence_items") as batch_op:
        batch_op.add_column(sa.Column("decision_version_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_evidence_items_decision_version_id",
            "decision_versions",
            ["decision_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.add_column(sa.Column("decision_version_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_recommendations_decision_version_id",
            "decision_versions",
            ["decision_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "score_cells",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("decision_version_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("criterion_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("evidence_item_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["decision_version_id"], ["decision_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["decision_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["criterion_id"], ["decision_criteria.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("score_cells")

    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.drop_constraint("fk_recommendations_decision_version_id", type_="foreignkey")
        batch_op.drop_column("decision_version_id")

    with op.batch_alter_table("evidence_items") as batch_op:
        batch_op.drop_constraint("fk_evidence_items_decision_version_id", type_="foreignkey")
        batch_op.drop_column("decision_version_id")

    with op.batch_alter_table("decision_criteria") as batch_op:
        batch_op.drop_constraint("fk_decision_criteria_decision_version_id", type_="foreignkey")
        batch_op.drop_column("is_locked")
        batch_op.drop_column("decision_version_id")

    with op.batch_alter_table("decision_candidates") as batch_op:
        batch_op.drop_constraint("fk_decision_candidates_decision_version_id", type_="foreignkey")
        batch_op.drop_column("is_locked")
        batch_op.drop_column("selection_state")
        batch_op.drop_column("decision_version_id")

    op.drop_table("decision_versions")
    sa.Enum(name="decision_version_status").drop(op.get_bind())
