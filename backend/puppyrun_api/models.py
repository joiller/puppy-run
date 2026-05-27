import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, Uuid
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


class DecisionCandidate(Base):
    __tablename__ = "decision_candidates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="candidates")


class DecisionCriterion(Base):
    __tablename__ = "decision_criteria"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    weight: Mapped[int] = mapped_column(nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_needed: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="criteria")


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
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


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id", ondelete="CASCADE"), nullable=False
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
