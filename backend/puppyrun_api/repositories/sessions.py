from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_api.models import (
    AgentEvent,
    AgentRun,
    AgentRunStatus,
    DecisionSession,
    DecisionSessionStatus,
)


def derive_title(prompt: str) -> str:
    compact = " ".join(prompt.split())
    return compact[:80] if len(compact) > 80 else compact


async def create_decision_session(db: AsyncSession, prompt: str) -> DecisionSession:
    session = DecisionSession(title=derive_title(prompt), prompt=prompt)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_decision_sessions(db: AsyncSession) -> list[DecisionSession]:
    result = await db.execute(select(DecisionSession).order_by(DecisionSession.created_at.desc()))
    return list(result.scalars())


async def get_decision_session(db: AsyncSession, session_id: UUID) -> DecisionSession | None:
    return await db.get(DecisionSession, session_id)


async def create_agent_run(db: AsyncSession, session_id: UUID) -> AgentRun:
    run = AgentRun(session_id=session_id, status=AgentRunStatus.queued)
    session = await db.get(DecisionSession, session_id)
    if session is not None:
        session.status = DecisionSessionStatus.queued
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def mark_run_started(db: AsyncSession, run_id: UUID) -> None:
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"agent run not found: {run_id}")
    session = await db.get(DecisionSession, run.session_id)
    run.status = AgentRunStatus.running
    if session is not None:
        session.status = DecisionSessionStatus.running
    db.add(AgentEvent(run_id=run.id, event_type="run_started", message="Dummy Agent run started"))
    await db.commit()


async def mark_run_completed(db: AsyncSession, run_id: UUID, summary: str) -> None:
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"agent run not found: {run_id}")
    session = await db.get(DecisionSession, run.session_id)
    run.status = AgentRunStatus.completed
    if session is not None:
        session.status = DecisionSessionStatus.completed
        session.current_summary = summary
    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="run_completed",
            message="Dummy Agent run completed",
            payload={"summary": summary},
        )
    )
    await db.commit()
