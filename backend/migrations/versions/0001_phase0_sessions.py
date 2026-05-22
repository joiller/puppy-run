"""create phase0 session tables

Revision ID: 0001_phase0_sessions
Revises:
Create Date: 2026-05-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_phase0_sessions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    decision_status = sa.Enum(
        "created",
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        name="decision_session_status",
    )
    run_status = sa.Enum("queued", "running", "completed", "failed", name="agent_run_status")
    op.create_table(
        "decision_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", decision_status, nullable=False),
        sa.Column("current_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("job_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "agent_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("agent_events")
    op.drop_table("agent_runs")
    op.drop_table("decision_sessions")
    sa.Enum(name="agent_run_status").drop(op.get_bind())
    sa.Enum(name="decision_session_status").drop(op.get_bind())
