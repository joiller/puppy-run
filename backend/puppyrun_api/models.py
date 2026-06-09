import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from puppyrun_api.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class DecisionSessionStatus(StrEnum):
    created = "created"
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AgentRunStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class DecisionVersionStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class RecursiveMutableDict(MutableDict):
    def __init__(self, *args, **kwargs) -> None:
        dict.__init__(self)
        self._nested_parent = None
        self.update(*args, **kwargs)

    @classmethod
    def coerce(cls, key, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(value)
        return super().coerce(key, value)

    def __setitem__(self, key, value) -> None:
        value = _coerce_json_value(value)
        _set_nested_parent(value, self)
        dict.__setitem__(self, key, value)
        self.changed()

    def update(self, *args, **kwargs) -> None:
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def setdefault(self, key, value=None):
        if key not in self:
            self[key] = value
        return self[key]

    def changed(self) -> None:
        super().changed()
        if self._nested_parent is not None:
            self._nested_parent.changed()


class RecursiveMutableList(MutableList):
    def __init__(self, iterable=()) -> None:
        list.__init__(self)
        self._nested_parent = None
        self.extend(iterable)

    @classmethod
    def coerce(cls, key, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, list):
            return cls(value)
        return super().coerce(key, value)

    def __setitem__(self, index, value) -> None:
        if isinstance(index, slice):
            value = [_coerce_json_value(item) for item in value]
            for item in value:
                _set_nested_parent(item, self)
        else:
            value = _coerce_json_value(value)
            _set_nested_parent(value, self)
        list.__setitem__(self, index, value)
        self.changed()

    def append(self, value) -> None:
        value = _coerce_json_value(value)
        _set_nested_parent(value, self)
        list.append(self, value)
        self.changed()

    def extend(self, values) -> None:
        for value in values:
            self.append(value)

    def insert(self, index, value) -> None:
        value = _coerce_json_value(value)
        _set_nested_parent(value, self)
        list.insert(self, index, value)
        self.changed()

    def changed(self) -> None:
        super().changed()
        if self._nested_parent is not None:
            self._nested_parent.changed()


def _coerce_json_value(value):
    if isinstance(value, (RecursiveMutableDict, RecursiveMutableList)):
        return value
    if isinstance(value, dict):
        return RecursiveMutableDict(value)
    if isinstance(value, list):
        return RecursiveMutableList(value)
    return value


def _set_nested_parent(value, parent) -> None:
    if isinstance(value, (RecursiveMutableDict, RecursiveMutableList)):
        value._nested_parent = parent


class DecisionSession(Base):
    __tablename__ = "decision_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DecisionSessionStatus] = mapped_column(
        Enum(DecisionSessionStatus, name="decision_session_status"),
        default=DecisionSessionStatus.created,
        nullable=False,
    )
    workflow_stage: Mapped[str] = mapped_column(String(80), default="created", nullable=False)
    decision_context: Mapped[dict] = mapped_column(
        RecursiveMutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    current_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    messages: Mapped[list["DecisionMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="DecisionMessage.created_at",
    )
    versions: Mapped[list["DecisionVersion"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="DecisionVersion.version_number",
    )
    candidates: Mapped[list["DecisionCandidate"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    criteria: Mapped[list["DecisionCriterion"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    evidence_items: Mapped[list["EvidenceItem"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    score_cells: Mapped[list["ScoreCell"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list["ToolCall"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    claims: Mapped[list["Claim"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    risk_signals: Mapped[list["RiskSignal"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    verification_tasks: Mapped[list["VerificationTask"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class DecisionMessage(Base):
    __tablename__ = "decision_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="messages")


class DecisionVersion(Base):
    __tablename__ = "decision_versions"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "version_number",
            name="uq_decision_versions_session_version_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[DecisionVersionStatus] = mapped_column(
        Enum(DecisionVersionStatus, name="decision_version_status"),
        default=DecisionVersionStatus.queued,
        nullable=False,
    )
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_versions.id", ondelete="SET NULL")
    )
    change_summary: Mapped[dict] = mapped_column(
        RecursiveMutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    gap_analysis: Mapped[dict] = mapped_column(
        RecursiveMutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    adr: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[DecisionSession] = relationship(back_populates="versions")
    candidates: Mapped[list["DecisionCandidate"]] = relationship(back_populates="version")
    criteria: Mapped[list["DecisionCriterion"]] = relationship(back_populates="version")
    evidence_items: Mapped[list["EvidenceItem"]] = relationship(back_populates="version")
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="version")
    score_cells: Mapped[list["ScoreCell"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list["ToolCall"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    claims: Mapped[list["Claim"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    risk_signals: Mapped[list["RiskSignal"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    verification_tasks: Mapped[list["VerificationTask"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class DecisionCandidate(Base):
    __tablename__ = "decision_candidates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    decision_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_versions.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    include_reason: Mapped[str] = mapped_column(Text, nullable=False)
    health_summary: Mapped[str | None] = mapped_column(Text)
    health_metrics: Mapped[dict] = mapped_column(
        RecursiveMutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    score: Mapped[int | None] = mapped_column(Integer)
    selection_state: Mapped[str] = mapped_column(String(40), default="included", nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="candidates")
    version: Mapped[DecisionVersion | None] = relationship(back_populates="candidates")


class DecisionCriterion(Base):
    __tablename__ = "decision_criteria"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    decision_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_versions.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    weight: Mapped[int] = mapped_column(nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_needed: Mapped[str] = mapped_column(Text, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="criteria")
    version: Mapped[DecisionVersion | None] = relationship(back_populates="criteria")


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    decision_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_versions.id", ondelete="SET NULL")
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_candidates.id", ondelete="SET NULL")
    )
    criterion_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_criteria.id", ondelete="SET NULL")
    )
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    credibility: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(
        RecursiveMutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="evidence_items")
    version: Mapped[DecisionVersion | None] = relationship(back_populates="evidence_items")


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_tool_calls_idempotency_key"),
        Index("ix_tool_calls_session_id", "session_id"),
        Index("ix_tool_calls_decision_version_id", "decision_version_id"),
        Index("ix_tool_calls_status_source", "status", "source_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    decision_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_versions.id", ondelete="CASCADE")
    )
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(80))
    source_url: Mapped[str | None] = mapped_column(Text)
    request_summary: Mapped[str | None] = mapped_column(Text)
    response_summary: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(
        RecursiveMutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    session: Mapped[DecisionSession] = relationship(back_populates="tool_calls")
    version: Mapped[DecisionVersion | None] = relationship(back_populates="tool_calls")


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        Index("ix_claims_session_id", "session_id"),
        Index("ix_claims_decision_version_id", "decision_version_id"),
        Index("ix_claims_candidate_id", "candidate_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    decision_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_versions.id", ondelete="CASCADE")
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_candidates.id", ondelete="CASCADE"), nullable=False
    )
    criterion_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_criteria.id", ondelete="SET NULL")
    )
    source_evidence_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("evidence_items.id", ondelete="SET NULL")
    )
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    citation_text: Mapped[str] = mapped_column(Text, nullable=False)
    credibility: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(
        RecursiveMutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    session: Mapped[DecisionSession] = relationship(back_populates="claims")
    version: Mapped[DecisionVersion | None] = relationship(back_populates="claims")
    candidate: Mapped[DecisionCandidate] = relationship()
    criterion: Mapped[DecisionCriterion | None] = relationship()
    source_evidence_item: Mapped[EvidenceItem | None] = relationship()


class RiskSignal(Base):
    __tablename__ = "risk_signals"
    __table_args__ = (
        Index("ix_risk_signals_session_id", "session_id"),
        Index("ix_risk_signals_decision_version_id", "decision_version_id"),
        Index("ix_risk_signals_candidate_id", "candidate_id"),
        Index("ix_risk_signals_status_severity", "status", "severity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    decision_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_versions.id", ondelete="CASCADE")
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_candidates.id", ondelete="CASCADE"), nullable=False
    )
    risk_key: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    credibility: Mapped[str] = mapped_column(String(40), nullable=False)
    score_impact: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    supporting_claim_ids: Mapped[list] = mapped_column(
        RecursiveMutableList.as_mutable(JSON), default=list, nullable=False
    )
    verification_task_ids: Mapped[list] = mapped_column(
        RecursiveMutableList.as_mutable(JSON), default=list, nullable=False
    )
    payload: Mapped[dict] = mapped_column(
        RecursiveMutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    session: Mapped[DecisionSession] = relationship(back_populates="risk_signals")
    version: Mapped[DecisionVersion | None] = relationship(back_populates="risk_signals")
    candidate: Mapped[DecisionCandidate] = relationship()
    verification_tasks: Mapped[list["VerificationTask"]] = relationship(
        back_populates="risk_signal"
    )


class VerificationTask(Base):
    __tablename__ = "verification_tasks"
    __table_args__ = (
        Index("ix_verification_tasks_session_id", "session_id"),
        Index("ix_verification_tasks_decision_version_id", "decision_version_id"),
        Index("ix_verification_tasks_candidate_id", "candidate_id"),
        Index("ix_verification_tasks_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    decision_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_versions.id", ondelete="CASCADE")
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_candidates.id", ondelete="CASCADE"), nullable=False
    )
    risk_signal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_signals.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    verification_question: Mapped[str] = mapped_column(Text, nullable=False)
    stronger_source_type: Mapped[str | None] = mapped_column(String(80))
    stronger_source_url: Mapped[str | None] = mapped_column(Text)
    verdict: Mapped[str | None] = mapped_column(String(40))
    rationale: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(
        RecursiveMutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    session: Mapped[DecisionSession] = relationship(back_populates="verification_tasks")
    version: Mapped[DecisionVersion | None] = relationship(back_populates="verification_tasks")
    candidate: Mapped[DecisionCandidate] = relationship()
    risk_signal: Mapped[RiskSignal] = relationship(back_populates="verification_tasks")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    decision_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_versions.id", ondelete="SET NULL")
    )
    recommended_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_candidates.id", ondelete="SET NULL")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[dict] = mapped_column(
        RecursiveMutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="recommendations")
    version: Mapped[DecisionVersion | None] = relationship(back_populates="recommendations")


class ScoreCell(Base):
    __tablename__ = "score_cells"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    decision_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_versions.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_candidates.id", ondelete="CASCADE"), nullable=False
    )
    criterion_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_criteria.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_item_ids: Mapped[list] = mapped_column(
        RecursiveMutableList.as_mutable(JSON), default=list, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="score_cells")
    version: Mapped[DecisionVersion] = relationship(back_populates="score_cells")
    candidate: Mapped[DecisionCandidate] = relationship()
    criterion: Mapped[DecisionCriterion] = relationship()


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, name="agent_run_status"),
        default=AgentRunStatus.queued,
        nullable=False,
    )
    job_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    session: Mapped[DecisionSession] = relationship(back_populates="agent_runs")
    events: Mapped[list["AgentEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[AgentRun] = relationship(back_populates="events")
