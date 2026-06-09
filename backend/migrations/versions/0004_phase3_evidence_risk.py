"""add phase3 evidence and risk tables

Revision ID: 0004_phase3_evidence_risk
Revises: 0003_phase2_versions
Create Date: 2026-06-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_phase3_evidence_risk"
down_revision = "0003_phase2_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("decision_version_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=300), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("request_summary", sa.Text(), nullable=True),
        sa.Column("response_summary", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["decision_version_id"], ["decision_versions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_tool_calls_idempotency_key"),
    )
    op.create_index("ix_tool_calls_session_id", "tool_calls", ["session_id"])
    op.create_index(
        "ix_tool_calls_decision_version_id", "tool_calls", ["decision_version_id"]
    )
    op.create_index(
        "ix_tool_calls_status_source", "tool_calls", ["status", "source_type"]
    )

    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("decision_version_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("criterion_id", sa.Uuid(), nullable=True),
        sa.Column("source_evidence_item_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("citation_text", sa.Text(), nullable=False),
        sa.Column("credibility", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["decision_version_id"], ["decision_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["decision_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["criterion_id"], ["decision_criteria.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_evidence_item_id"], ["evidence_items.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_claims_session_id", "claims", ["session_id"])
    op.create_index("ix_claims_decision_version_id", "claims", ["decision_version_id"])
    op.create_index("ix_claims_candidate_id", "claims", ["candidate_id"])

    op.create_table(
        "risk_signals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("decision_version_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("risk_key", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("credibility", sa.String(length=40), nullable=False),
        sa.Column("score_impact", sa.Integer(), nullable=False),
        sa.Column("supporting_claim_ids", sa.JSON(), nullable=False),
        sa.Column("verification_task_ids", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["decision_version_id"], ["decision_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["decision_candidates.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_risk_signals_session_id", "risk_signals", ["session_id"])
    op.create_index(
        "ix_risk_signals_decision_version_id", "risk_signals", ["decision_version_id"]
    )
    op.create_index("ix_risk_signals_candidate_id", "risk_signals", ["candidate_id"])
    op.create_index(
        "ix_risk_signals_status_severity", "risk_signals", ["status", "severity"]
    )

    op.create_table(
        "verification_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("decision_version_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("risk_signal_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("verification_question", sa.Text(), nullable=False),
        sa.Column("stronger_source_type", sa.String(length=80), nullable=True),
        sa.Column("stronger_source_url", sa.Text(), nullable=True),
        sa.Column("verdict", sa.String(length=40), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["decision_version_id"], ["decision_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["decision_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["risk_signal_id"], ["risk_signals.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_verification_tasks_session_id", "verification_tasks", ["session_id"])
    op.create_index(
        "ix_verification_tasks_decision_version_id",
        "verification_tasks",
        ["decision_version_id"],
    )
    op.create_index(
        "ix_verification_tasks_candidate_id", "verification_tasks", ["candidate_id"]
    )
    op.create_index("ix_verification_tasks_status", "verification_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_verification_tasks_status", table_name="verification_tasks")
    op.drop_index("ix_verification_tasks_candidate_id", table_name="verification_tasks")
    op.drop_index(
        "ix_verification_tasks_decision_version_id", table_name="verification_tasks"
    )
    op.drop_index("ix_verification_tasks_session_id", table_name="verification_tasks")
    op.drop_table("verification_tasks")

    op.drop_index("ix_risk_signals_status_severity", table_name="risk_signals")
    op.drop_index("ix_risk_signals_candidate_id", table_name="risk_signals")
    op.drop_index("ix_risk_signals_decision_version_id", table_name="risk_signals")
    op.drop_index("ix_risk_signals_session_id", table_name="risk_signals")
    op.drop_table("risk_signals")

    op.drop_index("ix_claims_candidate_id", table_name="claims")
    op.drop_index("ix_claims_decision_version_id", table_name="claims")
    op.drop_index("ix_claims_session_id", table_name="claims")
    op.drop_table("claims")

    op.drop_index("ix_tool_calls_status_source", table_name="tool_calls")
    op.drop_index("ix_tool_calls_decision_version_id", table_name="tool_calls")
    op.drop_index("ix_tool_calls_session_id", table_name="tool_calls")
    op.drop_table("tool_calls")
