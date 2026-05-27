"""add phase1 workspace tables

Revision ID: 0002_phase1_workspace
Revises: 0001_phase0_sessions
Create Date: 2026-05-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_phase1_workspace"
down_revision = "0001_phase0_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decision_sessions",
        sa.Column("workflow_stage", sa.String(length=80), nullable=False, server_default="created"),
    )
    op.add_column(
        "decision_sessions",
        sa.Column("decision_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_table(
        "decision_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "decision_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("repo_full_name", sa.String(length=200), nullable=False),
        sa.Column("include_reason", sa.Text(), nullable=False),
        sa.Column("health_summary", sa.Text(), nullable=True),
        sa.Column("health_metrics", sa.JSON(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "decision_criteria",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_needed", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("criterion_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("credibility", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["decision_candidates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["criterion_id"], ["decision_criteria.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "recommendations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("recommended_candidate_id", sa.Uuid(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["recommended_candidate_id"], ["decision_candidates.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("recommendations")
    op.drop_table("evidence_items")
    op.drop_table("decision_criteria")
    op.drop_table("decision_candidates")
    op.drop_table("decision_messages")
    op.drop_column("decision_sessions", "decision_context")
    op.drop_column("decision_sessions", "workflow_stage")
