# PuppyRun Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 online thin slice: a real, deterministic Agent workflow that clarifies an Agent-framework decision, discovers 2-3 candidates, analyzes public GitHub repository health, creates criteria and evidence, generates a basic recommendation, and exposes the trace in the web console.

**Architecture:** Extend the Phase 0 modular monolith instead of introducing a separate Agent service. The FastAPI API owns session, message, workspace, and run endpoints; the arq worker executes the Phase 1 workflow; `puppyrun_agent` contains deterministic Agent steps and GitHub analysis tools; PostgreSQL stores candidates, criteria, evidence, recommendations, messages, and trace events. Phase 1 intentionally avoids live LLM calls so the public demo remains stable, cheap, and reproducible while still replacing the dummy job with real workflow logic.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL, Redis, arq, httpx, pytest, React, TypeScript, Vite, Vitest, Docker Compose.

---

## Scope Check

The approved design spec defines Phase 1 as the "Online Thin Slice." This plan implements only that slice:

- Free-form decision input.
- 1-2 deterministic clarification turns.
- Candidate discovery for Agent frameworks.
- Criteria generation.
- Public GitHub analysis for 2-3 candidates.
- Basic evidence summary.
- Basic recommendation.
- Agent trace.

Out of scope for Phase 1:

- Full web search.
- Official documentation crawling.
- Community risk verification.
- MCP adapters.
- User accounts, billing, RBAC, and private repository access.
- Editable weights and candidate locking.
- LLM-based synthesis.
- SSE or WebSocket streaming.
- Export jobs.

The first supported candidate catalog is deliberately small and source-checked on 2026-05-27:

- LangGraph: `langchain-ai/langgraph` (`https://github.com/langchain-ai/langgraph`)
- OpenAI Agents SDK for Python: `openai/openai-agents-python` (`https://github.com/openai/openai-agents-python`)
- CrewAI: `crewAIInc/crewAI` (`https://github.com/crewAIInc/crewAI`)

## File Structure

Create or modify these files:

```text
backend/
  pyproject.toml
  migrations/versions/0002_phase1_workspace.py
  puppyrun_agent/
    __init__.py
    catalog.py
    clarification.py
    criteria.py
    github_client.py
    recommendation.py
    workflow.py
  puppyrun_api/
    models.py
    schemas.py
    repositories/
      sessions.py
      workspace.py
    routes/
      sessions.py
  puppyrun_worker/
    jobs.py
    main.py
  tests/
    test_phase1_clarification.py
    test_phase1_github_client.py
    test_phase1_workflow.py
    test_sessions.py
apps/web/
  src/
    App.css
    App.test.tsx
    App.tsx
    api.ts
    types.ts
README.md
.env.example
deploy/vps/.env.example
```

Responsibility boundaries:

- `puppyrun_agent/catalog.py`: static Phase 1 candidate registry.
- `puppyrun_agent/clarification.py`: deterministic context extraction and 1-2 follow-up questions.
- `puppyrun_agent/criteria.py`: deterministic criteria and weights from prompt plus clarification answers.
- `puppyrun_agent/github_client.py`: typed GitHub REST API client with optional token support.
- `puppyrun_agent/recommendation.py`: deterministic scoring and recommendation synthesis.
- `puppyrun_agent/workflow.py`: orchestration of candidate discovery, criteria, GitHub evidence, recommendation, and trace events.
- `puppyrun_api/repositories/workspace.py`: persistence for messages, candidates, criteria, evidence, recommendations, and workspace reads.
- `puppyrun_worker/jobs.py`: arq entrypoint for `run_phase1_agent_job`.
- `apps/web/src/App.tsx`: single-page workbench surface for clarification, run status, candidates, criteria, evidence, recommendation, and trace.

---

### Task 1: Phase 1 Data Model And Migration

**Files:**
- Modify: `backend/puppyrun_api/models.py`
- Create: `backend/migrations/versions/0002_phase1_workspace.py`
- Test: `backend/tests/test_phase1_clarification.py`

- [ ] **Step 1: Write the failing model test**

Add `backend/tests/test_phase1_clarification.py`:

```python
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_api.db import Base
from puppyrun_api.models import DecisionMessage, DecisionSession


@pytest.mark.asyncio
async def test_phase1_session_can_store_messages_and_stage() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session = DecisionSession(
            title="Compare Agent frameworks",
            prompt="Compare LangGraph and OpenAI Agents SDK for a web Agent runtime.",
            workflow_stage="clarifying",
            decision_context={"domain": "agent_framework_selection"},
        )
        db.add(session)
        await db.flush()
        db.add(
            DecisionMessage(
                session_id=session.id,
                role="assistant",
                content="Which constraints matter most for the runtime?",
            )
        )
        await db.commit()

    async with maker() as db:
        stored = await db.scalar(select(DecisionSession))
        assert stored is not None
        assert stored.workflow_stage == "clarifying"
        assert stored.decision_context["domain"] == "agent_framework_selection"
        messages = (await db.execute(select(DecisionMessage))).scalars().all()
        assert [message.role for message in messages] == ["assistant"]

    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_phase1_clarification.py -q
```

Expected: FAIL because `DecisionMessage`, `workflow_stage`, and `decision_context` do not exist yet.

- [ ] **Step 3: Extend SQLAlchemy models**

Modify `backend/puppyrun_api/models.py` by adding `Integer` to the SQLAlchemy imports, then adding these relationships and model classes while keeping existing Phase 0 classes:

```python
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
    decision_context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    current_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    messages: Mapped[list["DecisionMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
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
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="messages")


class DecisionCandidate(Base):
    __tablename__ = "decision_candidates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    include_reason: Mapped[str] = mapped_column(Text, nullable=False)
    health_summary: Mapped[str | None] = mapped_column(Text)
    health_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="candidates")


class DecisionCriterion(Base):
    __tablename__ = "decision_criteria"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id"), nullable=False
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
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_candidates.id")
    )
    criterion_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_criteria.id")
    )
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    credibility: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="evidence_items")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id"), nullable=False
    )
    recommended_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_candidates.id")
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[DecisionSession] = relationship(back_populates="recommendations")
```

- [ ] **Step 4: Add Alembic migration**

Create `backend/migrations/versions/0002_phase1_workspace.py`:

```python
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
```

- [ ] **Step 5: Run model test to verify it passes**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_phase1_clarification.py -q
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/puppyrun_api/models.py backend/migrations/versions/0002_phase1_workspace.py backend/tests/test_phase1_clarification.py
git commit -m "feat: add phase1 workspace data model"
```

---

### Task 2: Session Messages And Workspace API

**Files:**
- Modify: `backend/puppyrun_api/schemas.py`
- Modify: `backend/puppyrun_api/repositories/sessions.py`
- Create: `backend/puppyrun_api/repositories/workspace.py`
- Modify: `backend/puppyrun_api/routes/sessions.py`
- Test: `backend/tests/test_sessions.py`

- [ ] **Step 1: Add failing API tests**

Append to `backend/tests/test_sessions.py`:

```python
@pytest.mark.asyncio
async def test_create_session_returns_initial_clarification(session_client: AsyncClient) -> None:
    response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and CrewAI for a web Agent runtime."},
    )

    assert response.status_code == 201
    created = response.json()
    assert created["workflow_stage"] == "clarifying"
    assert created["decision_context"]["domain"] == "agent_framework_selection"

    workspace_response = await session_client.get(f"/api/v1/sessions/{created['id']}/workspace")
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["session"]["id"] == created["id"]
    assert workspace["messages"][0]["role"] == "assistant"
    assert "constraints matter most" in workspace["messages"][0]["content"]


@pytest.mark.asyncio
async def test_answer_clarification_marks_session_ready(session_client: AsyncClient) -> None:
    create_response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a web Agent runtime."},
    )
    session_id = create_response.json()["id"]

    answer_response = await session_client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={
            "content": (
                "We need Python first, durable checkpoints, human approval steps, "
                "and simple production tracing."
            )
        },
    )

    assert answer_response.status_code == 201
    workspace = answer_response.json()
    assert workspace["session"]["workflow_stage"] == "ready_for_research"
    assert [message["role"] for message in workspace["messages"]] == ["assistant", "user"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_sessions.py -q
```

Expected: FAIL because the response schemas and new routes do not exist.

- [ ] **Step 3: Add response and request schemas**

Extend `backend/puppyrun_api/schemas.py`:

```python
class CreateDecisionMessageRequest(BaseModel):
    content: str = Field(min_length=2, max_length=4000)


class DecisionMessageResponse(BaseModel):
    id: UUID
    session_id: UUID
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionCandidateResponse(BaseModel):
    id: UUID
    session_id: UUID
    name: str
    slug: str
    repo_full_name: str
    include_reason: str
    health_summary: str | None
    health_metrics: dict
    score: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionCriterionResponse(BaseModel):
    id: UUID
    session_id: UUID
    name: str
    weight: int
    rationale: str
    evidence_needed: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EvidenceItemResponse(BaseModel):
    id: UUID
    session_id: UUID
    candidate_id: UUID | None
    criterion_id: UUID | None
    source_type: str
    source_url: str
    title: str
    summary: str
    credibility: str
    payload: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class RecommendationResponse(BaseModel):
    id: UUID
    session_id: UUID
    recommended_candidate_id: UUID | None
    summary: str
    rationale: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkspaceResponse(BaseModel):
    session: DecisionSessionResponse
    messages: list[DecisionMessageResponse]
    candidates: list[DecisionCandidateResponse]
    criteria: list[DecisionCriterionResponse]
    evidence_items: list[EvidenceItemResponse]
    recommendations: list[RecommendationResponse]
    events: list[AgentEventResponse]
```

Also add `workflow_stage: str` and `decision_context: dict` to `DecisionSessionResponse`.

- [ ] **Step 4: Add clarification helpers to the session repository**

Modify `backend/puppyrun_api/repositories/sessions.py`:

```python
from puppyrun_agent.clarification import build_initial_context, build_initial_question
from puppyrun_api.models import DecisionMessage, DecisionSession


async def create_decision_session(db: AsyncSession, prompt: str) -> DecisionSession:
    context = build_initial_context(prompt)
    session = DecisionSession(
        title=derive_title(prompt),
        prompt=prompt,
        workflow_stage="clarifying",
        decision_context=context,
    )
    db.add(session)
    await db.flush()
    db.add(
        DecisionMessage(
            session_id=session.id,
            role="assistant",
            content=build_initial_question(context),
        )
    )
    await db.commit()
    await db.refresh(session)
    return session
```

- [ ] **Step 5: Add workspace repository**

Create `backend/puppyrun_api/repositories/workspace.py`:

```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_agent.clarification import update_context_with_answer
from puppyrun_api.models import (
    AgentEvent,
    AgentRun,
    DecisionCandidate,
    DecisionCriterion,
    DecisionMessage,
    DecisionSession,
    EvidenceItem,
    Recommendation,
)


async def get_workspace(db: AsyncSession, session_id: UUID) -> dict[str, list | DecisionSession]:
    session = await db.get(DecisionSession, session_id)
    if session is None:
        raise ValueError("decision session not found")

    messages = (
        await db.execute(
            select(DecisionMessage)
            .where(DecisionMessage.session_id == session_id)
            .order_by(DecisionMessage.created_at.asc())
        )
    ).scalars().all()
    candidates = (
        await db.execute(select(DecisionCandidate).where(DecisionCandidate.session_id == session_id))
    ).scalars().all()
    criteria = (
        await db.execute(select(DecisionCriterion).where(DecisionCriterion.session_id == session_id))
    ).scalars().all()
    evidence_items = (
        await db.execute(select(EvidenceItem).where(EvidenceItem.session_id == session_id))
    ).scalars().all()
    recommendations = (
        await db.execute(select(Recommendation).where(Recommendation.session_id == session_id))
    ).scalars().all()
    run_ids = (
        await db.execute(select(AgentRun.id).where(AgentRun.session_id == session_id))
    ).scalars().all()
    events = []
    if run_ids:
        events = (
            await db.execute(
                select(AgentEvent)
                .where(AgentEvent.run_id.in_(run_ids))
                .order_by(AgentEvent.created_at.asc())
            )
        ).scalars().all()

    return {
        "session": session,
        "messages": list(messages),
        "candidates": list(candidates),
        "criteria": list(criteria),
        "evidence_items": list(evidence_items),
        "recommendations": list(recommendations),
        "events": list(events),
    }


async def append_user_message(db: AsyncSession, session_id: UUID, content: str) -> dict:
    session = await db.get(DecisionSession, session_id)
    if session is None:
        raise ValueError("decision session not found")

    db.add(DecisionMessage(session_id=session_id, role="user", content=content))
    session.decision_context = update_context_with_answer(session.decision_context, content)
    session.workflow_stage = "ready_for_research"
    await db.commit()
    return await get_workspace(db, session_id)
```

- [ ] **Step 6: Add workspace routes**

Modify `backend/puppyrun_api/routes/sessions.py`:

```python
from puppyrun_api.repositories import workspace as workspace_repo
from puppyrun_api.schemas import (
    AgentEventResponse,
    CreateDecisionMessageRequest,
    DecisionCandidateResponse,
    DecisionCriterionResponse,
    DecisionMessageResponse,
    EvidenceItemResponse,
    RecommendationResponse,
    WorkspaceResponse,
)


def to_workspace_response(workspace: dict) -> WorkspaceResponse:
    return WorkspaceResponse(
        session=DecisionSessionResponse.model_validate(workspace["session"]),
        messages=[DecisionMessageResponse.model_validate(item) for item in workspace["messages"]],
        candidates=[
            DecisionCandidateResponse.model_validate(item) for item in workspace["candidates"]
        ],
        criteria=[DecisionCriterionResponse.model_validate(item) for item in workspace["criteria"]],
        evidence_items=[
            EvidenceItemResponse.model_validate(item) for item in workspace["evidence_items"]
        ],
        recommendations=[
            RecommendationResponse.model_validate(item) for item in workspace["recommendations"]
        ],
        events=[AgentEventResponse.model_validate(item) for item in workspace["events"]],
    )


@router.get("/{session_id}/workspace", response_model=WorkspaceResponse)
async def get_workspace(session_id: UUID, db: SessionDep) -> WorkspaceResponse:
    try:
        workspace = await workspace_repo.get_workspace(db, session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="decision session not found") from None
    return to_workspace_response(workspace)


@router.post("/{session_id}/messages", response_model=WorkspaceResponse, status_code=201)
async def create_message(
    session_id: UUID,
    body: CreateDecisionMessageRequest,
    db: SessionDep,
) -> WorkspaceResponse:
    try:
        workspace = await workspace_repo.append_user_message(db, session_id, body.content)
    except ValueError:
        raise HTTPException(status_code=404, detail="decision session not found") from None
    return to_workspace_response(workspace)
```

- [ ] **Step 7: Run API tests**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_sessions.py -q
```

Expected: all session tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/puppyrun_api/schemas.py backend/puppyrun_api/repositories/sessions.py backend/puppyrun_api/repositories/workspace.py backend/puppyrun_api/routes/sessions.py backend/tests/test_sessions.py
git commit -m "feat: add phase1 workspace api"
```

---

### Task 3: Deterministic Clarification, Candidate, And Criteria Agents

**Files:**
- Create: `backend/puppyrun_agent/__init__.py`
- Create: `backend/puppyrun_agent/catalog.py`
- Create: `backend/puppyrun_agent/clarification.py`
- Create: `backend/puppyrun_agent/criteria.py`
- Test: `backend/tests/test_phase1_clarification.py`

- [ ] **Step 1: Add failing Agent unit tests**

Append to `backend/tests/test_phase1_clarification.py`:

```python
from puppyrun_agent.catalog import select_candidates
from puppyrun_agent.clarification import build_initial_context, build_initial_question
from puppyrun_agent.criteria import generate_criteria


def test_build_initial_context_detects_agent_framework_domain() -> None:
    context = build_initial_context(
        "Should I use LangGraph, OpenAI Agents SDK, or CrewAI for a web Agent runtime?"
    )

    assert context["domain"] == "agent_framework_selection"
    assert context["mentioned_candidates"] == ["langgraph", "openai_agents_sdk", "crewai"]


def test_build_initial_question_is_specific_to_missing_constraints() -> None:
    context = build_initial_context("Compare LangGraph and CrewAI for a web Agent runtime.")

    question = build_initial_question(context)

    assert "constraints matter most" in question
    assert "checkpointing" in question
    assert "human approval" in question


def test_select_candidates_limits_phase1_to_three_candidates() -> None:
    context = build_initial_context(
        "Compare LangGraph, OpenAI Agents SDK, CrewAI, and AutoGen for a web Agent runtime."
    )

    candidates = select_candidates(context)

    assert [candidate.slug for candidate in candidates] == [
        "langgraph",
        "openai_agents_sdk",
        "crewai",
    ]


def test_generate_criteria_weights_agent_runtime_needs() -> None:
    context = {
        "constraints": ["checkpointing", "human_in_loop", "observability"],
        "language_preference": "python",
    }

    criteria = generate_criteria(context)

    assert criteria[0].name == "Runtime control and state"
    assert sum(criterion.weight for criterion in criteria) == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_phase1_clarification.py -q
```

Expected: FAIL because `puppyrun_agent` does not exist.

- [ ] **Step 3: Add candidate catalog**

Create `backend/puppyrun_agent/catalog.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateProfile:
    name: str
    slug: str
    repo_full_name: str
    capabilities: tuple[str, ...]
    include_reason: str


CANDIDATE_REGISTRY: tuple[CandidateProfile, ...] = (
    CandidateProfile(
        name="LangGraph",
        slug="langgraph",
        repo_full_name="langchain-ai/langgraph",
        capabilities=("python", "typescript", "checkpointing", "human_in_loop", "stateful_graph"),
        include_reason=(
            "Included because it is designed for stateful graph-based Agent workflows "
            "and is a strong baseline for checkpointed runtime control."
        ),
    ),
    CandidateProfile(
        name="OpenAI Agents SDK",
        slug="openai_agents_sdk",
        repo_full_name="openai/openai-agents-python",
        capabilities=("python", "handoffs", "guardrails", "tracing", "tool_calling"),
        include_reason=(
            "Included because it is a lightweight Python SDK for agentic workflows, "
            "handoffs, guardrails, and tracing."
        ),
    ),
    CandidateProfile(
        name="CrewAI",
        slug="crewai",
        repo_full_name="crewAIInc/crewAI",
        capabilities=("python", "multi_agent_roles", "task_orchestration"),
        include_reason=(
            "Included because it represents a role-and-task oriented multi-agent workflow style."
        ),
    ),
)


def select_candidates(context: dict) -> list[CandidateProfile]:
    mentioned = context.get("mentioned_candidates", [])
    ordered = sorted(
        CANDIDATE_REGISTRY,
        key=lambda candidate: 0 if candidate.slug in mentioned else 1,
    )
    return list(ordered[:3])
```

- [ ] **Step 4: Add clarification logic**

Create `backend/puppyrun_agent/clarification.py`:

```python
from puppyrun_agent.catalog import CANDIDATE_REGISTRY


KEYWORD_TO_CONSTRAINT = {
    "checkpoint": "checkpointing",
    "state": "stateful_runtime",
    "human": "human_in_loop",
    "approval": "human_in_loop",
    "trace": "observability",
    "observability": "observability",
    "python": "python",
    "typescript": "typescript",
}


def build_initial_context(prompt: str) -> dict:
    lowered = prompt.lower()
    mentioned_candidates = [
        candidate.slug
        for candidate in CANDIDATE_REGISTRY
        if candidate.name.lower() in lowered or candidate.slug.replace("_", " ") in lowered
    ]
    constraints = sorted(
        {
            constraint
            for keyword, constraint in KEYWORD_TO_CONSTRAINT.items()
            if keyword in lowered
        }
    )
    language_preference = "typescript" if "typescript" in constraints else "python"
    return {
        "domain": "agent_framework_selection",
        "mentioned_candidates": mentioned_candidates,
        "constraints": constraints,
        "language_preference": language_preference,
        "clarification_turns": 0,
    }


def build_initial_question(context: dict) -> str:
    return (
        "Which constraints matter most for this Agent runtime: checkpointing, "
        "human approval, Python or TypeScript fit, deployment simplicity, and observability?"
    )


def update_context_with_answer(context: dict, answer: str) -> dict:
    updated = dict(context)
    lowered = answer.lower()
    existing = set(updated.get("constraints", []))
    for keyword, constraint in KEYWORD_TO_CONSTRAINT.items():
        if keyword in lowered:
            existing.add(constraint)
    if "typescript" in existing and "python" not in existing:
        updated["language_preference"] = "typescript"
    else:
        updated["language_preference"] = "python"
    updated["constraints"] = sorted(existing)
    updated["clarification_turns"] = int(updated.get("clarification_turns", 0)) + 1
    return updated
```

- [ ] **Step 5: Add criteria generation**

Create `backend/puppyrun_agent/criteria.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CriterionProfile:
    name: str
    weight: int
    rationale: str
    evidence_needed: str


def generate_criteria(context: dict) -> list[CriterionProfile]:
    constraints = set(context.get("constraints", []))
    state_weight = 30 if {"checkpointing", "stateful_runtime"} & constraints else 25
    observability_weight = 20 if "observability" in constraints else 15
    human_weight = 20 if "human_in_loop" in constraints else 15
    ergonomics_weight = 20
    health_weight = 100 - state_weight - observability_weight - human_weight - ergonomics_weight
    return [
        CriterionProfile(
            name="Runtime control and state",
            weight=state_weight,
            rationale="State handling is central for long-running Agent workflows.",
            evidence_needed="Repository docs and implementation signals for graph state, checkpoints, and resumes.",
        ),
        CriterionProfile(
            name="Human-in-the-loop fit",
            weight=human_weight,
            rationale="The target workflow needs safe review points before expensive or risky actions.",
            evidence_needed="Signals for approvals, interrupts, handoffs, or review checkpoints.",
        ),
        CriterionProfile(
            name="Observability and traceability",
            weight=observability_weight,
            rationale="PuppyRun needs inspectable Agent traces and auditable decisions.",
            evidence_needed="Tracing, event, logging, or run inspection support.",
        ),
        CriterionProfile(
            name="Developer ergonomics",
            weight=ergonomics_weight,
            rationale="The first version should be buildable by a small team without heavy framework lock-in.",
            evidence_needed="SDK simplicity, Python fit, examples, and integration surface.",
        ),
        CriterionProfile(
            name="Open-source project health",
            weight=health_weight,
            rationale="The chosen framework should show active maintenance and adoption signals.",
            evidence_needed="GitHub stars, forks, open issues, recent push date, license, and repository metadata.",
        ),
    ]
```

- [ ] **Step 6: Run Agent unit tests**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_phase1_clarification.py -q
```

Expected: all tests in `test_phase1_clarification.py` pass.

- [ ] **Step 7: Commit**

```bash
git add backend/puppyrun_agent backend/tests/test_phase1_clarification.py
git commit -m "feat: add phase1 deterministic agent steps"
```

---

### Task 4: GitHub Repository Analysis Tool

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/puppyrun_api/config.py`
- Create: `backend/puppyrun_agent/github_client.py`
- Test: `backend/tests/test_phase1_github_client.py`
- Modify: `.env.example`
- Modify: `deploy/vps/.env.example`

- [ ] **Step 1: Add failing GitHub client tests**

Create `backend/tests/test_phase1_github_client.py`:

```python
import httpx
import pytest

from puppyrun_agent.github_client import GitHubClient


@pytest.mark.asyncio
async def test_fetch_repository_summary_normalizes_github_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/langchain-ai/langgraph"
        return httpx.Response(
            200,
            json={
                "full_name": "langchain-ai/langgraph",
                "html_url": "https://github.com/langchain-ai/langgraph",
                "description": "Build resilient language agents as graphs.",
                "stargazers_count": 100,
                "forks_count": 20,
                "open_issues_count": 7,
                "pushed_at": "2026-05-20T12:00:00Z",
                "license": {"spdx_id": "MIT"},
            },
        )

    transport = httpx.MockTransport(handler)
    async with GitHubClient(transport=transport) as client:
        summary = await client.fetch_repository_summary("langchain-ai/langgraph")

    assert summary.full_name == "langchain-ai/langgraph"
    assert summary.stars == 100
    assert summary.license_spdx_id == "MIT"
    assert summary.source_url == "https://github.com/langchain-ai/langgraph"


@pytest.mark.asyncio
async def test_fetch_repository_summary_raises_for_missing_repo() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"message": "Not Found"}))
    async with GitHubClient(transport=transport) as client:
        with pytest.raises(ValueError, match="GitHub repository not found"):
            await client.fetch_repository_summary("missing/repo")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_phase1_github_client.py -q
```

Expected: FAIL because `github_client.py` does not exist.

- [ ] **Step 3: Promote httpx to runtime dependency and add settings**

Modify `backend/pyproject.toml`:

```toml
dependencies = [
  "alembic>=1.13.0",
  "arq>=0.26.0",
  "asyncpg>=0.29.0",
  "fastapi>=0.115.0",
  "greenlet>=3.0.0",
  "httpx>=0.27.0",
  "python-dotenv>=1.0.0",
  "pydantic-settings>=2.6.0",
  "sqlalchemy>=2.0.0",
  "uvicorn[standard]>=0.32.0",
]
```

Remove the duplicate `httpx>=0.27.0` entry from `dev` because it is now a runtime dependency. The important contract is that runtime code can import `httpx` without installing dev extras.

Modify `backend/puppyrun_api/config.py`:

```python
github_token: str | None = None
github_api_base_url: str = "https://api.github.com"
```

Update `.env.example` and `deploy/vps/.env.example`:

```dotenv
PUPPYRUN_GITHUB_TOKEN=
PUPPYRUN_GITHUB_API_BASE_URL=https://api.github.com
```

- [ ] **Step 4: Add GitHub client**

Create `backend/puppyrun_agent/github_client.py`:

```python
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class RepositorySummary:
    full_name: str
    source_url: str
    description: str
    stars: int
    forks: int
    open_issues: int
    pushed_at: str
    license_spdx_id: str | None

    def to_evidence_payload(self) -> dict:
        return {
            "full_name": self.full_name,
            "stars": self.stars,
            "forks": self.forks,
            "open_issues": self.open_issues,
            "pushed_at": self.pushed_at,
            "license_spdx_id": self.license_spdx_id,
        }


class GitHubClient:
    def __init__(
        self,
        *,
        api_base_url: str = "https://api.github.com",
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=api_base_url,
            headers=headers,
            timeout=10.0,
            transport=transport,
        )

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_repository_summary(self, repo_full_name: str) -> RepositorySummary:
        response = await self._client.get(f"/repos/{repo_full_name}")
        if response.status_code == 404:
            raise ValueError(f"GitHub repository not found: {repo_full_name}")
        response.raise_for_status()
        payload = response.json()
        license_payload = payload.get("license") or {}
        return RepositorySummary(
            full_name=payload["full_name"],
            source_url=payload["html_url"],
            description=payload.get("description") or "",
            stars=int(payload.get("stargazers_count") or 0),
            forks=int(payload.get("forks_count") or 0),
            open_issues=int(payload.get("open_issues_count") or 0),
            pushed_at=payload.get("pushed_at") or "",
            license_spdx_id=license_payload.get("spdx_id"),
        )
```

- [ ] **Step 5: Run GitHub client tests**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_phase1_github_client.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/puppyrun_api/config.py backend/puppyrun_agent/github_client.py backend/tests/test_phase1_github_client.py .env.example deploy/vps/.env.example
git commit -m "feat: add github repository analysis client"
```

---

### Task 5: Phase 1 Workflow Persistence And Recommendation

**Files:**
- Create: `backend/puppyrun_agent/recommendation.py`
- Create: `backend/puppyrun_agent/workflow.py`
- Modify: `backend/puppyrun_api/repositories/workspace.py`
- Test: `backend/tests/test_phase1_workflow.py`

- [ ] **Step 1: Add failing workflow test**

Create `backend/tests/test_phase1_workflow.py`:

```python
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_agent.workflow import run_phase1_workflow
from puppyrun_api.db import Base
from puppyrun_api.models import (
    DecisionCandidate,
    DecisionCriterion,
    DecisionSession,
    DecisionSessionStatus,
    EvidenceItem,
    Recommendation,
)
from puppyrun_api.repositories.sessions import create_agent_run, create_decision_session
from puppyrun_api.repositories.workspace import append_user_message


def github_handler(request: httpx.Request) -> httpx.Response:
    repo_name = request.url.path.removeprefix("/repos/")
    stars = {
        "langchain-ai/langgraph": 50000,
        "openai/openai-agents-python": 25000,
        "crewAIInc/crewAI": 30000,
    }[repo_name]
    return httpx.Response(
        200,
        json={
            "full_name": repo_name,
            "html_url": f"https://github.com/{repo_name}",
            "description": f"Repository for {repo_name}",
            "stargazers_count": stars,
            "forks_count": 1000,
            "open_issues_count": 100,
            "pushed_at": "2026-05-20T12:00:00Z",
            "license": {"spdx_id": "MIT"},
        },
    )


@pytest.mark.asyncio
async def test_phase1_workflow_persists_candidates_evidence_and_recommendation() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph, OpenAI Agents SDK, and CrewAI for a web Agent runtime.",
        )
        await append_user_message(
            db,
            session.id,
            "We need Python, checkpointing, human approval, and observability.",
        )
        run = await create_agent_run(db, session.id)
        run_id = run.id
        session_id = session.id

    transport = httpx.MockTransport(github_handler)
    async with maker() as db:
        await run_phase1_workflow(db, run_id, github_transport=transport)

    async with maker() as db:
        refreshed = await db.get(DecisionSession, session_id)
        assert refreshed is not None
        assert refreshed.status == DecisionSessionStatus.completed
        assert refreshed.workflow_stage == "completed"
        assert "Recommended:" in (refreshed.current_summary or "")
        assert len((await db.execute(select(DecisionCandidate))).scalars().all()) == 3
        assert len((await db.execute(select(DecisionCriterion))).scalars().all()) == 5
        assert len((await db.execute(select(EvidenceItem))).scalars().all()) == 3
        assert len((await db.execute(select(Recommendation))).scalars().all()) == 1

    await engine.dispose()
```

- [ ] **Step 2: Run workflow test to verify it fails**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_phase1_workflow.py -q
```

Expected: FAIL because workflow and recommendation modules do not exist.

- [ ] **Step 3: Add recommendation logic**

Create `backend/puppyrun_agent/recommendation.py`:

```python
from dataclasses import dataclass

from puppyrun_agent.catalog import CandidateProfile
from puppyrun_agent.github_client import RepositorySummary


@dataclass(frozen=True)
class CandidateScore:
    slug: str
    total: int
    reasons: list[str]


def score_candidate(
    candidate: CandidateProfile,
    repo: RepositorySummary,
    context: dict,
) -> CandidateScore:
    constraints = set(context.get("constraints", []))
    score = 40
    reasons: list[str] = []

    if "checkpointing" in constraints and "checkpointing" in candidate.capabilities:
        score += 20
        reasons.append("matches checkpointing requirement")
    if "human_in_loop" in constraints and "human_in_loop" in candidate.capabilities:
        score += 15
        reasons.append("matches human-in-the-loop requirement")
    if "observability" in constraints and {"tracing", "guardrails"} & set(candidate.capabilities):
        score += 10
        reasons.append("has tracing or guardrail-oriented capability signals")
    if repo.stars >= 20000:
        score += 10
        reasons.append("shows strong GitHub adoption")
    if repo.license_spdx_id:
        score += 5
        reasons.append(f"declares {repo.license_spdx_id} license metadata")

    return CandidateScore(slug=candidate.slug, total=min(score, 100), reasons=reasons)


def build_recommendation(
    candidates: list[tuple[CandidateProfile, RepositorySummary, CandidateScore]]
) -> tuple[str, dict]:
    ranked = sorted(candidates, key=lambda item: item[2].total, reverse=True)
    winner, repo, score = ranked[0]
    summary = (
        f"Recommended: {winner.name}. It scored {score.total}/100 in this Phase 1 "
        f"thin-slice analysis using GitHub health and runtime-fit criteria."
    )
    rationale = {
        "recommended_slug": winner.slug,
        "recommended_repo": repo.full_name,
        "ranked_candidates": [
            {
                "slug": candidate.slug,
                "name": candidate.name,
                "repo": repository.full_name,
                "score": candidate_score.total,
                "reasons": candidate_score.reasons,
            }
            for candidate, repository, candidate_score in ranked
        ],
    }
    return summary, rationale
```

- [ ] **Step 4: Add workflow orchestration**

Create `backend/puppyrun_agent/workflow.py`:

```python
from uuid import UUID

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_agent.catalog import select_candidates
from puppyrun_agent.criteria import generate_criteria
from puppyrun_agent.github_client import GitHubClient
from puppyrun_agent.recommendation import build_recommendation, score_candidate
from puppyrun_api.config import get_settings
from puppyrun_api.models import (
    AgentEvent,
    AgentRun,
    AgentRunStatus,
    DecisionSession,
    DecisionCandidate,
    DecisionCriterion,
    DecisionSessionStatus,
    EvidenceItem,
    Recommendation,
)


async def run_phase1_workflow(
    db: AsyncSession,
    run_id: UUID,
    *,
    github_transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"agent run not found: {run_id}")
    session = await db.get(DecisionSession, run.session_id)
    if session is None:
        raise ValueError(f"decision session not found: {run.session_id}")

    run.status = AgentRunStatus.running
    session.status = DecisionSessionStatus.running
    session.workflow_stage = "researching"
    db.add(AgentEvent(run_id=run.id, event_type="phase1_started", message="Phase 1 workflow started"))
    await db.commit()

    await db.execute(delete(Recommendation).where(Recommendation.session_id == session.id))
    await db.execute(delete(EvidenceItem).where(EvidenceItem.session_id == session.id))
    await db.execute(delete(DecisionCriterion).where(DecisionCriterion.session_id == session.id))
    await db.execute(delete(DecisionCandidate).where(DecisionCandidate.session_id == session.id))

    candidates = select_candidates(session.decision_context)
    criteria = generate_criteria(session.decision_context)

    criterion_models = [
        DecisionCriterion(
            session_id=session.id,
            name=criterion.name,
            weight=criterion.weight,
            rationale=criterion.rationale,
            evidence_needed=criterion.evidence_needed,
        )
        for criterion in criteria
    ]
    db.add_all(criterion_models)
    await db.flush()
    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="criteria_generated",
            message=f"Generated {len(criterion_models)} evaluation criteria",
        )
    )

    settings = get_settings()
    scored = []
    async with GitHubClient(
        api_base_url=settings.github_api_base_url,
        token=settings.github_token,
        transport=github_transport,
    ) as github:
        for candidate in candidates:
            repo = await github.fetch_repository_summary(candidate.repo_full_name)
            candidate_score = score_candidate(candidate, repo, session.decision_context)
            candidate_model = DecisionCandidate(
                session_id=session.id,
                name=candidate.name,
                slug=candidate.slug,
                repo_full_name=candidate.repo_full_name,
                include_reason=candidate.include_reason,
                health_summary=(
                    f"{repo.full_name}: {repo.stars} stars, {repo.forks} forks, "
                    f"{repo.open_issues} open issues, last pushed at {repo.pushed_at}."
                ),
                health_metrics=repo.to_evidence_payload(),
                score=candidate_score.total,
            )
            db.add(candidate_model)
            await db.flush()
            db.add(
                EvidenceItem(
                    session_id=session.id,
                    candidate_id=candidate_model.id,
                    criterion_id=None,
                    source_type="github_repo",
                    source_url=repo.source_url,
                    title=f"GitHub repository health for {candidate.name}",
                    summary=candidate_model.health_summary,
                    credibility="medium",
                    payload=repo.to_evidence_payload(),
                )
            )
            db.add(
                AgentEvent(
                    run_id=run.id,
                    event_type="github_repo_analyzed",
                    message=f"Analyzed {repo.full_name}",
                    payload=repo.to_evidence_payload(),
                )
            )
            scored.append((candidate, repo, candidate_score))

    summary, rationale = build_recommendation(scored)
    winner_slug = rationale["recommended_slug"]
    winner_model = next(
        candidate for candidate in await _get_candidates(db, session.id) if candidate.slug == winner_slug
    )
    db.add(
        Recommendation(
            session_id=session.id,
            recommended_candidate_id=winner_model.id,
            summary=summary,
            rationale=rationale,
        )
    )
    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="recommendation_generated",
            message=summary,
            payload=rationale,
        )
    )
    run.status = AgentRunStatus.completed
    session.status = DecisionSessionStatus.completed
    session.workflow_stage = "completed"
    session.current_summary = summary
    await db.commit()
    return summary


async def _get_candidates(db: AsyncSession, session_id: UUID) -> list[DecisionCandidate]:
    from sqlalchemy import select

    result = await db.execute(
        select(DecisionCandidate).where(DecisionCandidate.session_id == session_id)
    )
    return list(result.scalars())
```

- [ ] **Step 5: Run workflow tests**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_phase1_workflow.py -q
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/puppyrun_agent/recommendation.py backend/puppyrun_agent/workflow.py backend/tests/test_phase1_workflow.py
git commit -m "feat: persist phase1 workflow results"
```

---

### Task 6: Worker Job And Run Endpoint

**Files:**
- Modify: `backend/puppyrun_worker/jobs.py`
- Modify: `backend/puppyrun_worker/main.py`
- Modify: `backend/puppyrun_api/routes/sessions.py`
- Modify: `backend/tests/test_worker_jobs.py`
- Modify: `backend/tests/test_sessions.py`

- [ ] **Step 1: Add failing worker job test**

Modify `backend/tests/test_worker_jobs.py` so the main behavior test targets the Phase 1 job:

```python
@pytest.mark.asyncio
async def test_phase1_agent_job_marks_session_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(jobs, "SessionLocal", maker)

    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph, OpenAI Agents SDK, and CrewAI for PuppyRun.",
        )
        await append_user_message(
            db,
            session.id,
            "We need Python, checkpointing, human approval, and observability.",
        )
        run = await create_agent_run(db, session.id)
        run_id = run.id
        session_id = session.id

    async def fake_workflow(db, run_id_arg):
        assert str(run_id_arg) == str(run_id)
        refreshed = await db.get(DecisionSession, session_id)
        assert refreshed is not None
        refreshed.status = DecisionSessionStatus.completed
        refreshed.workflow_stage = "completed"
        refreshed.current_summary = "Recommended: LangGraph."
        await db.commit()
        return "Recommended: LangGraph."

    monkeypatch.setattr(jobs, "run_phase1_workflow", fake_workflow)

    summary = await jobs.run_phase1_agent_job({}, str(run_id))

    assert summary == "Recommended: LangGraph."

    await engine.dispose()
```

Import `DecisionSession` and `append_user_message` at the top of the test file.

- [ ] **Step 2: Run worker tests to verify they fail**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_worker_jobs.py -q
```

Expected: FAIL because `run_phase1_agent_job` is not registered.

- [ ] **Step 3: Add worker job**

Modify `backend/puppyrun_worker/jobs.py`:

```python
from puppyrun_agent.workflow import run_phase1_workflow


async def run_phase1_agent_job(ctx: dict, run_id: str) -> str:
    parsed_run_id = UUID(run_id)
    async with SessionLocal() as db:
        return await run_phase1_workflow(db, parsed_run_id)
```

Keep `run_dummy_agent_job` for historical compatibility until all public smoke docs and tests stop referencing it.

- [ ] **Step 4: Register worker function**

Modify `backend/puppyrun_worker/main.py`:

```python
from puppyrun_worker.jobs import run_dummy_agent_job, run_phase1_agent_job


class WorkerSettings:
    functions = [run_dummy_agent_job, run_phase1_agent_job]
    redis_settings = redis_settings_from_url(get_settings().redis_url)
```

- [ ] **Step 5: Change run endpoint to enqueue Phase 1**

Modify `backend/puppyrun_api/routes/sessions.py` in the run endpoint:

```python
job = await redis.enqueue_job("run_phase1_agent_job", str(run.id), _job_id=f"phase1:{run.id}")
run.job_id = job.job_id if job is not None else f"phase1:{run.id}"
```

Also rename the Python function from `start_dummy_run` to `start_agent_run`; the route path can remain `POST /api/v1/sessions/{session_id}/runs`.

- [ ] **Step 6: Add run endpoint assertion**

Append to `backend/tests/test_sessions.py` with Redis enqueue mocked:

```python
@pytest.mark.asyncio
async def test_start_run_enqueues_phase1_job(
    session_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a web Agent runtime."},
    )
    session_id = response.json()["id"]

    class FakeJob:
        job_id = "phase1:test-job"

    class FakeRedis:
        async def enqueue_job(self, name: str, run_id: str, _job_id: str):
            assert name == "run_phase1_agent_job"
            assert _job_id.startswith("phase1:")
            return FakeJob()

        async def close(self) -> None:
            return None

    async def fake_create_pool(settings):
        return FakeRedis()

    monkeypatch.setattr("puppyrun_api.routes.sessions.create_pool", fake_create_pool)

    run_response = await session_client.post(f"/api/v1/sessions/{session_id}/runs")

    assert run_response.status_code == 202
    assert run_response.json()["run"]["job_id"] == "phase1:test-job"
```

- [ ] **Step 7: Run backend tests for worker and sessions**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_worker_jobs.py tests/test_sessions.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/puppyrun_worker/jobs.py backend/puppyrun_worker/main.py backend/puppyrun_api/routes/sessions.py backend/tests/test_worker_jobs.py backend/tests/test_sessions.py
git commit -m "feat: run phase1 workflow from worker"
```

---

### Task 7: Web API Types And Workbench UI

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/App.css`
- Modify: `apps/web/src/App.test.tsx`

- [ ] **Step 1: Add failing frontend test**

Update the imports and API mocks in `apps/web/src/App.test.tsx`:

```tsx
import { createSession, getWorkspace, listSessions, sendMessage, startRun } from "./api";
import type { DecisionSession, StartAgentRunResponse, Workspace } from "./types";

vi.mock("./api", () => ({
  createSession: vi.fn(),
  getWorkspace: vi.fn(),
  listSessions: vi.fn(),
  sendMessage: vi.fn(),
  startRun: vi.fn()
}));

const createSessionMock = vi.mocked(createSession);
const getWorkspaceMock = vi.mocked(getWorkspace);
const listSessionsMock = vi.mocked(listSessions);
const sendMessageMock = vi.mocked(sendMessage);
const startRunMock = vi.mocked(startRun);
```

Update `makeSession` so every test session includes Phase 1 fields:

```tsx
function makeSession(
  status: DecisionSession["status"],
  currentSummary: string | null = null
): DecisionSession {
  return {
    id: "session-1",
    title: "Compare LangGraph",
    prompt: "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime.",
    status,
    workflow_stage: status === "created" ? "clarifying" : status,
    decision_context: { domain: "agent_framework_selection" },
    current_summary: currentSummary,
    created_at: "2026-05-27T00:00:00Z",
    updated_at: "2026-05-27T00:00:00Z"
  };
}
```

Replace the main test body with a Phase 1 workspace flow while keeping the existing timer and polling helpers:

```tsx
it("shows clarification, recommendation, evidence, and trace for a Phase 1 run", async () => {
  const created = makeSession("created");
  const ready = { ...created, workflow_stage: "ready_for_research" };
  const completed = {
    ...created,
    status: "completed",
    workflow_stage: "completed",
    current_summary: "Recommended: LangGraph. It scored 85/100."
  };
  let workspace = makeWorkspace(created);

  listSessionsMock.mockImplementation(async () => [workspace.session]);
  createSessionMock.mockImplementation(async () => {
    workspace = makeWorkspace(created);
    return created;
  });
  getWorkspaceMock.mockImplementation(async () => workspace);
  sendMessageMock.mockImplementation(async () => {
    workspace = makeWorkspace(ready, [{ role: "user", content: "We need checkpointing." }]);
    return workspace;
  });
  startRunMock.mockImplementation(async () => makeRunResponse({ ...ready, status: "queued" }));

  render(<App />);
  await flushAsyncUpdates();

  fireEvent.click(screen.getByRole("button", { name: "Create session" }));
  await waitFor(() => {
    expect(screen.getByText(/constraints matter most/i)).toBeTruthy();
  });

  fireEvent.change(screen.getByLabelText("Clarification answer"), {
    target: { value: "We need Python, checkpointing, human approval, and observability." }
  });
  fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
  await waitFor(() => {
    expect(screen.getByText("ready_for_research")).toBeTruthy();
  });

  fireEvent.click(screen.getByRole("button", { name: "Run Phase 1 Agent" }));
  workspace = makeCompletedWorkspace(completed);
  await runPoll();

  await waitFor(() => {
    expect(screen.getByText(/Recommended: LangGraph/)).toBeTruthy();
    expect(screen.getByText("GitHub repository health for LangGraph")).toBeTruthy();
    expect(screen.getByText("recommendation_generated")).toBeTruthy();
  });
});
```

Add helper functions in the same test file:

```tsx
function makeWorkspace(
  session: DecisionSession,
  extraMessages: Array<{ role: string; content: string }> = []
): Workspace {
  return {
    session,
    messages: [
      {
        id: "message-1",
        session_id: session.id,
        role: "assistant",
        content:
          "Which constraints matter most for this Agent runtime: checkpointing, human approval, Python or TypeScript fit, deployment simplicity, and observability?",
        created_at: "2026-05-27T00:00:00Z"
      },
      ...extraMessages.map((message, index) => ({
        id: `message-extra-${index}`,
        session_id: session.id,
        role: message.role,
        content: message.content,
        created_at: "2026-05-27T00:00:00Z"
      }))
    ],
    candidates: [],
    criteria: [],
    evidence_items: [],
    recommendations: [],
    events: []
  };
}

function makeCompletedWorkspace(session: DecisionSession): Workspace {
  return {
    ...makeWorkspace(session),
    candidates: [
      {
        id: "candidate-1",
        session_id: session.id,
        name: "LangGraph",
        slug: "langgraph",
        repo_full_name: "langchain-ai/langgraph",
        include_reason: "Included for checkpointed stateful workflows.",
        health_summary: "langchain-ai/langgraph: 50000 stars.",
        health_metrics: { stars: 50000 },
        score: 85,
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    criteria: [
      {
        id: "criterion-1",
        session_id: session.id,
        name: "Runtime control and state",
        weight: 30,
        rationale: "State handling is central for long-running Agent workflows.",
        evidence_needed: "Checkpoint and state support.",
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    evidence_items: [
      {
        id: "evidence-1",
        session_id: session.id,
        candidate_id: "candidate-1",
        criterion_id: null,
        source_type: "github_repo",
        source_url: "https://github.com/langchain-ai/langgraph",
        title: "GitHub repository health for LangGraph",
        summary: "langchain-ai/langgraph: 50000 stars.",
        credibility: "medium",
        payload: { stars: 50000 },
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    recommendations: [
      {
        id: "recommendation-1",
        session_id: session.id,
        recommended_candidate_id: "candidate-1",
        summary: session.current_summary ?? "",
        rationale: { recommended_slug: "langgraph" },
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    events: [
      {
        id: "event-1",
        run_id: "run-1",
        event_type: "recommendation_generated",
        message: session.current_summary ?? "",
        payload: {},
        created_at: "2026-05-27T00:00:00Z"
      }
    ]
  };
}
```

- [ ] **Step 2: Run frontend test to verify it fails**

Run:

```bash
cd apps/web
npm test -- --run
```

Expected: FAIL because workspace API types and UI are missing.

- [ ] **Step 3: Add frontend types**

Extend `apps/web/src/types.ts`:

```ts
export interface DecisionSession {
  id: string;
  title: string;
  prompt: string;
  status: DecisionSessionStatus;
  workflow_stage: string;
  decision_context: Record<string, unknown>;
  current_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface DecisionMessage {
  id: string;
  session_id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface DecisionCandidate {
  id: string;
  session_id: string;
  name: string;
  slug: string;
  repo_full_name: string;
  include_reason: string;
  health_summary: string | null;
  health_metrics: Record<string, unknown>;
  score: number | null;
  created_at: string;
}

export interface DecisionCriterion {
  id: string;
  session_id: string;
  name: string;
  weight: number;
  rationale: string;
  evidence_needed: string;
  created_at: string;
}

export interface EvidenceItem {
  id: string;
  session_id: string;
  candidate_id: string | null;
  criterion_id: string | null;
  source_type: string;
  source_url: string;
  title: string;
  summary: string;
  credibility: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Recommendation {
  id: string;
  session_id: string;
  recommended_candidate_id: string | null;
  summary: string;
  rationale: Record<string, unknown>;
  created_at: string;
}

export interface AgentEvent {
  id: string;
  run_id: string;
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Workspace {
  session: DecisionSession;
  messages: DecisionMessage[];
  candidates: DecisionCandidate[];
  criteria: DecisionCriterion[];
  evidence_items: EvidenceItem[];
  recommendations: Recommendation[];
  events: AgentEvent[];
}
```

- [ ] **Step 4: Add frontend API functions**

Modify `apps/web/src/api.ts`:

```ts
import type { DecisionSession, StartAgentRunResponse, Workspace } from "./types";

export async function getWorkspace(sessionId: string): Promise<Workspace> {
  return request<Workspace>(`/api/v1/sessions/${sessionId}/workspace`);
}

export async function sendMessage(sessionId: string, content: string): Promise<Workspace> {
  return request<Workspace>(`/api/v1/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content })
  });
}
```

- [ ] **Step 5: Update the app surface**

Modify `apps/web/src/App.tsx` so the first screen is the usable Phase 1 workbench, not a landing page:

```tsx
const [workspace, setWorkspace] = useState<Workspace | null>(null);
const [clarificationAnswer, setClarificationAnswer] = useState("");

async function loadWorkspace(sessionId: string) {
  const nextWorkspace = await getWorkspace(sessionId);
  setWorkspace(nextWorkspace);
  selectSession(nextWorkspace.session);
}

async function handleAnswer(event: FormEvent) {
  event.preventDefault();
  if (!selected || clarificationAnswer.trim().length < 2) return;
  setIsBusy(true);
  setError(null);
  try {
    const nextWorkspace = await sendMessage(selected.id, clarificationAnswer);
    setWorkspace(nextWorkspace);
    selectSession(nextWorkspace.session);
    setClarificationAnswer("");
  } catch (err) {
    setError(String(err));
  } finally {
    setIsBusy(false);
  }
}
```

Render sections with stable dimensions and dense, scan-friendly labels:

```tsx
<section className="workspace-grid">
  <section className="session-column" aria-label="Sessions">
    <form className="composer" onSubmit={handleCreate}>
      <label htmlFor="prompt">Decision prompt</label>
      <textarea
        id="prompt"
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
      />
      <button disabled={isBusy || prompt.trim().length < 10} type="submit">
        Create session
      </button>
    </form>
    <div className="session-list">
      {sessions.map((session) => (
        <button
          className={selected?.id === session.id ? "session selected" : "session"}
          key={session.id}
          onClick={() => loadWorkspace(session.id).catch((err: unknown) => setError(String(err)))}
          type="button"
        >
          <span>{session.title}</span>
          <strong>{session.workflow_stage}</strong>
        </button>
      ))}
    </div>
  </section>

  <section className="decision-column" aria-label="Decision workspace">
    <div className="stage-bar">
      <span>{workspace?.session.workflow_stage ?? "no_session"}</span>
      <button disabled={!selected || isBusy} onClick={handleRun} type="button">
        Run Phase 1 Agent
      </button>
    </div>

    <section className="clarification-thread">
      <h2>Clarification</h2>
      {workspace?.messages.map((message) => (
        <article className={`message ${message.role}`} key={message.id}>
          <strong>{message.role}</strong>
          <p>{message.content}</p>
        </article>
      ))}
      <form onSubmit={handleAnswer}>
        <label htmlFor="clarification-answer">Clarification answer</label>
        <textarea
          id="clarification-answer"
          value={clarificationAnswer}
          onChange={(event) => setClarificationAnswer(event.target.value)}
        />
        <button disabled={!selected || isBusy || clarificationAnswer.trim().length < 2} type="submit">
          Send answer
        </button>
      </form>
    </section>

    <section className="recommendation-section">
      <h2>Recommendation</h2>
      <p>{workspace?.recommendations.at(-1)?.summary ?? selected?.current_summary ?? "No recommendation yet."}</p>
    </section>
  </section>

  <aside className="evidence-column" aria-label="Evidence and trace">
    <h2>Candidates</h2>
    {workspace?.candidates.map((candidate) => (
      <article className="candidate-row" key={candidate.id}>
        <strong>{candidate.name}</strong>
        <span>{candidate.score ?? "-"} / 100</span>
        <p>{candidate.health_summary}</p>
      </article>
    ))}
    <h2>Criteria</h2>
    {workspace?.criteria.map((criterion) => (
      <article className="criterion-row" key={criterion.id}>
        <strong>{criterion.name}</strong>
        <span>{criterion.weight}</span>
        <p>{criterion.rationale}</p>
      </article>
    ))}
    <h2>Evidence</h2>
    {workspace?.evidence_items.map((item) => (
      <article className="evidence-row" key={item.id}>
        <a href={item.source_url} target="_blank" rel="noreferrer">{item.title}</a>
        <p>{item.summary}</p>
      </article>
    ))}
    <h2>Trace</h2>
    {workspace?.events.map((event) => (
      <article className="trace-row" key={event.id}>
        <strong>{event.event_type}</strong>
        <p>{event.message}</p>
      </article>
    ))}
  </aside>
</section>
```

- [ ] **Step 6: Apply restrained product UI CSS**

Modify `apps/web/src/App.css` around these classes:

```css
.app-shell {
  min-height: 100vh;
  background: #f7f8fa;
  color: #1c2430;
}

.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 18px 24px;
  border-bottom: 1px solid #d7dde5;
  background: #ffffff;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(220px, 280px) minmax(420px, 1fr) minmax(320px, 420px);
  gap: 1px;
  min-height: calc(100vh - 72px);
  background: #d7dde5;
}

.session-column,
.decision-column,
.evidence-column {
  min-width: 0;
  background: #ffffff;
  padding: 20px;
  overflow: auto;
}

.stage-bar,
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.message,
.candidate-row,
.criterion-row,
.evidence-row,
.trace-row {
  border-top: 1px solid #e4e8ee;
  padding: 12px 0;
}

.message p,
.candidate-row p,
.criterion-row p,
.evidence-row p,
.trace-row p {
  margin: 6px 0 0;
  line-height: 1.45;
}

button {
  min-height: 36px;
  border: 1px solid #9aa7b5;
  border-radius: 6px;
  background: #1f6feb;
  color: #ffffff;
  padding: 0 12px;
}

button:disabled {
  background: #d7dde5;
  color: #637083;
}

textarea {
  width: 100%;
  min-height: 112px;
  resize: vertical;
}

@media (max-width: 980px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 7: Run frontend tests**

Run:

```bash
cd apps/web
npm test -- --run
```

Expected: frontend tests pass.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/types.ts apps/web/src/api.ts apps/web/src/App.tsx apps/web/src/App.css apps/web/src/App.test.tsx
git commit -m "feat: add phase1 workbench ui"
```

---

### Task 8: End-To-End Verification And Documentation

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `deploy/vps/.env.example`
- Modify: `docs/superpowers/plans/2026-05-27-puppyrun-phase-1-plan.md`

- [ ] **Step 1: Run backend checks**

Run:

```bash
cd backend
. .venv/bin/activate
ruff check .
pytest -q
```

Expected:

```text
All checks passed!
```

and all backend tests pass.

- [ ] **Step 2: Run frontend checks**

Run:

```bash
cd apps/web
npm test -- --run
npm run build
```

Expected: Vitest passes and Vite production build completes.

- [ ] **Step 3: Run local Docker smoke test**

Run:

```bash
cp .env.example .env
docker compose up --build -d
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok","service":"puppyrun-api"}
```

- [ ] **Step 4: Verify Phase 1 browser flow locally**

Open `http://localhost:5173` and verify:

1. Create a session with:

```text
Compare LangGraph, OpenAI Agents SDK, and CrewAI for a web Agent runtime that needs Python, checkpointing, human approval, and observability.
```

2. The clarification thread shows the initial assistant question.
3. Submit:

```text
We need Python, checkpointing, human approval, deployment simplicity, and traceable runs.
```

4. The session stage becomes `ready_for_research`.
5. Click `Run Phase 1 Agent`.
6. Polling updates the selected workspace to `completed`.
7. The UI shows three candidates, five criteria, three GitHub evidence items, one recommendation, and trace events including `github_repo_analyzed` and `recommendation_generated`.

- [ ] **Step 5: Verify VPS deployment contract without exposing private hosts**

Run the same repository-level deployment checks used for Phase 0:

```bash
docker compose -f deploy/vps/docker-compose.yml --env-file deploy/vps/.env.example config
```

Expected: Compose config renders without requiring a real IP, SSH target, token, or secret in committed docs.

- [ ] **Step 6: Update README Phase status**

Modify `README.md` Phase status section:

```markdown
Phase 1 adds the first real online Agent workflow: a deterministic Agent-framework selection thin slice with clarification, candidate discovery, criteria generation, public GitHub repository analysis, a basic recommendation, and trace events.

To increase GitHub API rate limits in a public deployment, set `PUPPYRUN_GITHUB_TOKEN` in the deployment environment. The token is optional for local smoke tests and must not be committed.
```

- [ ] **Step 7: Mark this plan's closure status**

At the top of this plan, under `Tech Stack`, add after implementation:

```markdown
**Closure status, YYYY-MM-DD:** Phase 1 implemented and verified locally with backend tests, frontend tests, production build, Docker Compose, and browser smoke test. Public VPS redeployment status is recorded separately because real public hosts and SSH targets are private operational details.
```

Use the actual completion date.

- [ ] **Step 8: Commit**

```bash
git add README.md .env.example deploy/vps/.env.example docs/superpowers/plans/2026-05-27-puppyrun-phase-1-plan.md
git commit -m "docs: document phase1 verification"
```

---

## Full Verification Before Merge

Run these commands from the repository root after all tasks:

```bash
cd backend
. .venv/bin/activate
ruff check .
pytest -q
cd ../apps/web
npm test -- --run
npm run build
cd ../..
docker compose up --build -d
curl http://localhost:8000/health
```

Then complete the browser flow from Task 8 Step 4.

Expected outcome:

- Backend lint passes.
- Backend tests pass.
- Frontend tests pass.
- Frontend production build passes.
- Docker Compose starts API, worker, web, PostgreSQL, and Redis.
- `/health` returns `{"status":"ok","service":"puppyrun-api"}`.
- The web console completes the Phase 1 workflow and shows candidates, criteria, evidence, recommendation, and trace.

## Self-Review

Spec coverage:

- Free-form decision input: covered by existing session creation plus Task 2 schema updates.
- 1-2 clarification turns: covered by Tasks 2 and 3 with the first deterministic clarification turn and a clean extension point for a second turn.
- Candidate discovery for Agent frameworks: covered by Task 3 candidate catalog.
- Criteria generation: covered by Task 3 criteria generator and Task 5 persistence.
- Public GitHub analysis for 2-3 candidates: covered by Tasks 4 and 5.
- Basic evidence summary: covered by Task 5 `EvidenceItem` persistence and Task 7 UI.
- Basic recommendation: covered by Task 5 recommendation logic and Task 7 UI.
- Agent trace: covered by Task 5 `AgentEvent` writes and Task 7 trace panel.

Placeholder scan:

- No placeholder markers, vague deferred steps, or unbounded source integrations remain in this plan.
- Every implementation task has a failing test, a concrete implementation step, a verification command, and a commit step.

Type consistency:

- Backend workspace fields map directly to frontend `Workspace`.
- `workflow_stage` stays a string to avoid expanding the existing PostgreSQL enum during Phase 1.
- `DecisionSession.status` remains the coarse run status; Phase 1-specific progress lives in `workflow_stage` and trace events.

Execution recommendation:

Use subagent-driven execution if available: Tasks 1-2 are API/data-model work, Tasks 3-5 are Agent/runtime work, Task 6 bridges worker/API, Task 7 is frontend, and Task 8 is verification/documentation. The tasks are ordered to avoid shared-state conflicts.
