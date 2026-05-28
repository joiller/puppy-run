from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


@dataclass(frozen=True)
class Workspace:
    session: DecisionSession
    messages: list[DecisionMessage]
    candidates: list[DecisionCandidate]
    criteria: list[DecisionCriterion]
    evidence_items: list[EvidenceItem]
    recommendations: list[Recommendation]
    events: list[AgentEvent]


async def get_workspace(db: AsyncSession, session_id: UUID) -> Workspace:
    session = await db.get(DecisionSession, session_id)
    if session is None:
        raise ValueError(f"decision session not found: {session_id}")

    messages = await _list_by_session(db, DecisionMessage, session_id)
    candidates = await _list_by_session(db, DecisionCandidate, session_id)
    criteria = await _list_by_session(db, DecisionCriterion, session_id)
    evidence_items = await _list_by_session(db, EvidenceItem, session_id)
    recommendations = await _list_by_session(db, Recommendation, session_id)
    events = await _list_events(db, session_id)

    return Workspace(
        session=session,
        messages=messages,
        candidates=candidates,
        criteria=criteria,
        evidence_items=evidence_items,
        recommendations=recommendations,
        events=events,
    )


async def append_user_message(db: AsyncSession, session_id: UUID, content: str) -> Workspace:
    session = await db.get(DecisionSession, session_id)
    if session is None:
        raise ValueError(f"decision session not found: {session_id}")

    stripped_content = content.strip()
    context = session.decision_context
    clarification = context.setdefault("clarification", {})
    clarification["status"] = "answered"
    clarification["answer"] = stripped_content
    session.workflow_stage = "ready_for_research"

    db.add(DecisionMessage(session_id=session.id, role="user", content=stripped_content))
    await db.commit()
    return await get_workspace(db, session_id)


async def _list_by_session(db: AsyncSession, model, session_id: UUID) -> list:
    result = await db.execute(
        select(model)
        .where(model.session_id == session_id)
        .order_by(model.created_at.asc(), model.id.asc())
    )
    return list(result.scalars())


async def _list_events(db: AsyncSession, session_id: UUID) -> list[AgentEvent]:
    result = await db.execute(
        select(AgentEvent)
        .join(AgentRun)
        .where(AgentRun.session_id == session_id)
        .order_by(AgentEvent.created_at.asc(), AgentEvent.id.asc())
    )
    return list(result.scalars())
