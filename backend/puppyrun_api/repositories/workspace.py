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
    DecisionVersion,
    DecisionVersionStatus,
    EvidenceItem,
    Recommendation,
    ScoreCell,
)


@dataclass(frozen=True)
class Workspace:
    session: DecisionSession
    messages: list[DecisionMessage]
    versions: list[DecisionVersion]
    active_version: DecisionVersion | None
    draft: dict
    gap_analysis: dict
    candidates: list[DecisionCandidate]
    criteria: list[DecisionCriterion]
    evidence_items: list[EvidenceItem]
    score_cells: list[ScoreCell]
    recommendations: list[Recommendation]
    events: list[AgentEvent]


async def get_workspace(
    db: AsyncSession,
    session_id: UUID,
    version_id: UUID | None = None,
) -> Workspace:
    session = await db.get(DecisionSession, session_id)
    if session is None:
        raise ValueError(f"decision session not found: {session_id}")

    versions = await _list_versions(db, session_id)
    active_version = _choose_active_version(versions, version_id)
    messages = await _list_by_session(db, DecisionMessage, session_id)
    candidates = await _list_versioned_rows(
        db, DecisionCandidate, session_id, versions, active_version
    )
    criteria = await _list_versioned_rows(
        db, DecisionCriterion, session_id, versions, active_version
    )
    evidence_items = await _list_versioned_rows(
        db, EvidenceItem, session_id, versions, active_version
    )
    recommendations = await _list_versioned_rows(
        db, Recommendation, session_id, versions, active_version
    )
    score_cells = await _list_versioned_rows(db, ScoreCell, session_id, versions, active_version)
    events = await _list_events(db, session_id)

    return Workspace(
        session=session,
        messages=messages,
        versions=versions,
        active_version=active_version,
        draft=_phase2_draft(session.decision_context),
        gap_analysis=_gap_analysis(session.decision_context),
        candidates=candidates,
        criteria=criteria,
        evidence_items=evidence_items,
        score_cells=score_cells,
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


async def _list_versions(db: AsyncSession, session_id: UUID) -> list[DecisionVersion]:
    result = await db.execute(
        select(DecisionVersion)
        .where(DecisionVersion.session_id == session_id)
        .order_by(DecisionVersion.version_number.asc(), DecisionVersion.created_at.asc())
    )
    return list(result.scalars())


def _choose_active_version(
    versions: list[DecisionVersion],
    version_id: UUID | None,
) -> DecisionVersion | None:
    if version_id is not None:
        for version in versions:
            if version.id == version_id:
                return version
        raise ValueError(f"decision version not found: {version_id}")

    readable_statuses = {DecisionVersionStatus.completed, DecisionVersionStatus.running}
    for version in reversed(versions):
        if version.status in readable_statuses:
            return version
    return None


async def _list_versioned_rows(
    db: AsyncSession,
    model,
    session_id: UUID,
    versions: list[DecisionVersion],
    active_version: DecisionVersion | None,
) -> list:
    statement = select(model).where(model.session_id == session_id)
    if active_version is not None:
        statement = statement.where(model.decision_version_id == active_version.id)
    elif versions:
        statement = statement.where(model.decision_version_id.is_(None))
    result = await db.execute(statement.order_by(model.created_at.asc(), model.id.asc()))
    return list(result.scalars())


def _phase2_draft(context: dict) -> dict:
    draft = context.get("phase2_draft") if isinstance(context, dict) else None
    return {
        "source_version_id": None,
        "candidate_overrides": {},
        "custom_candidates": {},
        "must_include_constraints": {},
        "must_exclude_constraints": {},
        "weight_overrides": {},
        **(draft if isinstance(draft, dict) else {}),
    }


def _gap_analysis(context: dict) -> dict:
    gap_analysis = context.get("phase2_gap_analysis") if isinstance(context, dict) else None
    return {
        "requires_research": False,
        "score_only": False,
        "changed_candidates": [],
        "changed_constraints": [],
        "changed_weights": [],
        "research_tasks": [],
        "reuse_tasks": [],
        "items": [],
        **(gap_analysis if isinstance(gap_analysis, dict) else {}),
    }


async def _list_events(db: AsyncSession, session_id: UUID) -> list[AgentEvent]:
    result = await db.execute(
        select(AgentEvent)
        .join(AgentRun)
        .where(AgentRun.session_id == session_id)
        .order_by(AgentEvent.created_at.asc(), AgentEvent.id.asc())
    )
    return list(result.scalars())
