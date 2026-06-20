from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_agent.clarification import build_initial_context, build_initial_question
from puppyrun_agent.phase2 import normalize_phase2_draft
from puppyrun_api.models import (
    AgentEvent,
    AgentRun,
    AgentRunStatus,
    DecisionMessage,
    DecisionSession,
    DecisionSessionStatus,
    DecisionVersion,
    DecisionVersionStatus,
)


class Phase2VersionConflictError(ValueError):
    pass


def derive_title(prompt: str) -> str:
    compact = " ".join(prompt.split())
    return compact[:80] if len(compact) > 80 else compact


def build_initial_decision_context(prompt: str) -> dict:
    context = build_initial_context(prompt)
    return {
        **context,
        "original_prompt": prompt,
        "clarification": {
            "status": "pending",
            "question": build_initial_question(context),
        },
    }


def build_initial_clarification_message(context: dict) -> str:
    return str(context["clarification"]["question"])


async def create_decision_session(db: AsyncSession, prompt: str) -> DecisionSession:
    decision_context = build_initial_decision_context(prompt)
    session = DecisionSession(
        title=derive_title(prompt),
        prompt=prompt,
        workflow_stage="clarifying",
        decision_context=decision_context,
    )
    db.add(session)
    await db.flush()
    db.add(
        DecisionMessage(
            session_id=session.id,
            role="assistant",
            content=build_initial_clarification_message(decision_context),
        )
    )
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


async def validate_phase2_version_request(db: AsyncSession, session_id: UUID) -> None:
    await _phase2_version_request_context(db, session_id)


async def create_phase2_version_run(
    db: AsyncSession,
    session_id: UUID,
) -> tuple[AgentRun, DecisionVersion]:
    session, draft, source_version = await _phase2_version_request_context(db, session_id)
    draft = normalize_phase2_draft(draft, source_version.id)

    run = AgentRun(session_id=session_id, status=AgentRunStatus.queued)
    db.add(run)
    await db.flush()

    version = DecisionVersion(
        session_id=session_id,
        version_number=await _next_version_number(db, session_id),
        label="Phase 2 targeted revision",
        status=DecisionVersionStatus.queued,
        source_version_id=source_version.id,
        change_summary={
            "kind": "phase2_targeted",
            "agent_run_id": str(run.id),
            "source_version_id": str(source_version.id),
            "phase2_draft": draft,
        },
        gap_analysis=_phase2_gap_analysis(session.decision_context),
    )
    db.add(version)
    session.status = DecisionSessionStatus.queued
    session.workflow_stage = "queued"
    await db.commit()
    await db.refresh(run)
    await db.refresh(version)
    return run, version


async def mark_phase2_version_enqueue_failed(
    db: AsyncSession,
    run_id: UUID,
    exc: Exception,
) -> None:
    await db.rollback()

    run = await db.get(AgentRun, run_id)
    if run is None:
        return

    session = await db.get(DecisionSession, run.session_id)
    version = await _phase2_version_for_run(db, run)
    message = str(exc)
    failure = {
        "error": message,
        "error_type": type(exc).__name__,
        "phase": "enqueue",
    }

    run.status = AgentRunStatus.failed
    if version is not None:
        version.status = DecisionVersionStatus.failed
        version.gap_analysis = {
            **dict(version.gap_analysis or {}),
            "failure": failure,
        }
        if version.adr is None:
            version.adr = f"Phase 2 failed before enqueue: {message}"

    if session is not None:
        session.status = DecisionSessionStatus.failed
        session.workflow_stage = "failed"

    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="phase2_enqueue_failed",
            message=f"Phase 2 enqueue failed: {message}",
            payload={
                "version_id": str(version.id) if version is not None else None,
                **failure,
            },
        )
    )
    await db.commit()


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


async def _phase2_version_request_context(
    db: AsyncSession,
    session_id: UUID,
) -> tuple[DecisionSession, dict, DecisionVersion]:
    session = await db.get(DecisionSession, session_id)
    if session is None:
        raise ValueError(f"decision session not found: {session_id}")

    raw_draft = _phase2_draft(session.decision_context)
    draft = normalize_phase2_draft(raw_draft, None)
    if raw_draft is None or not _draft_has_changes(draft):
        raise Phase2VersionConflictError("no phase2 draft changes found")

    source_version = await _completed_source_version(db, session_id, draft.get("source_version_id"))
    if source_version is None:
        raise Phase2VersionConflictError("no completed source version found")

    return session, draft, source_version


def _phase2_draft(context: dict | None) -> dict | None:
    draft = context.get("phase2_draft") if isinstance(context, dict) else None
    return draft if isinstance(draft, dict) else None


def _phase2_gap_analysis(context: dict | None) -> dict:
    gap_analysis = context.get("phase2_gap_analysis") if isinstance(context, dict) else None
    return dict(gap_analysis) if isinstance(gap_analysis, dict) else {}


def _draft_has_changes(draft: dict) -> bool:
    return any(
        bool(draft.get(key))
        for key in (
            "candidate_overrides",
            "custom_candidates",
            "must_include_constraints",
            "must_exclude_constraints",
            "weight_overrides",
        )
    )


async def _completed_source_version(
    db: AsyncSession,
    session_id: UUID,
    source_version_id: str | UUID | None,
) -> DecisionVersion | None:
    statement = (
        select(DecisionVersion)
        .where(DecisionVersion.session_id == session_id)
        .where(DecisionVersion.status == DecisionVersionStatus.completed)
    )
    if source_version_id is not None:
        statement = statement.where(DecisionVersion.id == UUID(str(source_version_id)))
    else:
        statement = statement.order_by(DecisionVersion.version_number.desc()).limit(1)
    return (await db.execute(statement)).scalar_one_or_none()


async def _phase2_version_for_run(
    db: AsyncSession,
    run: AgentRun,
) -> DecisionVersion | None:
    result = await db.execute(
        select(DecisionVersion)
        .where(DecisionVersion.session_id == run.session_id)
        .order_by(DecisionVersion.version_number.desc(), DecisionVersion.created_at.desc())
    )
    for version in result.scalars():
        if str((version.change_summary or {}).get("agent_run_id")) == str(run.id):
            return version
    return None


async def _next_version_number(db: AsyncSession, session_id: UUID) -> int:
    current_max = await db.scalar(
        select(func.max(DecisionVersion.version_number)).where(
            DecisionVersion.session_id == session_id
        )
    )
    return int(current_max or 0) + 1
