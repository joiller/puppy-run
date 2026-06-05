from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_agent.phase2 import (
    apply_phase2_constraints,
    apply_phase2_criteria,
    build_gap_analysis,
    build_phase2_candidates,
    normalize_phase2_draft,
)
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


class Phase2DraftConflictError(ValueError):
    pass


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


async def update_phase2_draft(
    db: AsyncSession,
    session_id: UUID,
    raw_draft: dict,
) -> Workspace:
    session = await db.get(DecisionSession, session_id)
    if session is None:
        raise ValueError(f"decision session not found: {session_id}")

    versions = await _list_versions(db, session_id)
    source_version = _choose_source_version(versions, raw_draft.get("source_version_id"))
    source_version_requested = raw_draft.get("source_version_id") is not None
    has_legacy_baseline = await _has_legacy_baseline(db, session_id)
    if source_version is None and (source_version_requested or not has_legacy_baseline):
        raise Phase2DraftConflictError("no completed source version or legacy baseline found")

    source_version_id = source_version.id if source_version is not None else None
    source_workspace = await get_workspace(db, session_id, version_id=source_version_id)
    normalized_draft = normalize_phase2_draft(raw_draft, source_version_id)
    baseline_context = {
        **(session.decision_context or {}),
        "candidates": _candidate_snapshots(source_workspace.candidates),
    }
    effective_context = apply_phase2_constraints(baseline_context, normalized_draft)
    next_candidates = build_phase2_candidates(baseline_context, normalized_draft)
    next_criteria = apply_phase2_criteria(
        _criterion_snapshots(source_workspace.criteria),
        normalized_draft,
        effective_context,
    )
    gap_analysis = build_gap_analysis(
        normalized_draft,
        next_candidates,
        next_criteria,
        _evidence_snapshots(source_workspace.evidence_items, source_workspace.candidates),
    )

    context = dict(session.decision_context or {})
    context["phase2_draft"] = normalized_draft
    context["phase2_gap_analysis"] = gap_analysis
    session.decision_context = context
    session.workflow_stage = "context_changed"
    await db.commit()
    await db.refresh(session)
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


def _choose_source_version(
    versions: list[DecisionVersion],
    source_version_id: str | UUID | None,
) -> DecisionVersion | None:
    completed_versions = [
        version for version in versions if version.status == DecisionVersionStatus.completed
    ]
    if source_version_id is not None:
        requested_source_version_id = UUID(str(source_version_id))
        for version in completed_versions:
            if version.id == requested_source_version_id:
                return version
        return None
    return completed_versions[-1] if completed_versions else None


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


async def _has_legacy_baseline(db: AsyncSession, session_id: UUID) -> bool:
    result = await db.execute(
        select(DecisionCandidate.id)
        .where(DecisionCandidate.session_id == session_id)
        .where(DecisionCandidate.decision_version_id.is_(None))
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _candidate_snapshots(candidates: list[DecisionCandidate]) -> list[dict]:
    return [
        {
            "name": candidate.name,
            "slug": candidate.slug,
            "repo_full_name": candidate.repo_full_name,
            "include_reason": candidate.include_reason,
            "selection_state": candidate.selection_state,
            "is_locked": candidate.is_locked,
        }
        for candidate in candidates
    ]


def _criterion_snapshots(criteria: list[DecisionCriterion]) -> list[dict]:
    return [
        {
            "name": criterion.name,
            "weight": criterion.weight,
            "rationale": criterion.rationale,
            "evidence_needed": criterion.evidence_needed,
            "is_locked": criterion.is_locked,
        }
        for criterion in criteria
    ]


def _evidence_snapshots(
    evidence_items: list[EvidenceItem],
    candidates: list[DecisionCandidate],
) -> list[dict]:
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    snapshots = []
    for evidence_item in evidence_items:
        candidate = (
            candidates_by_id.get(evidence_item.candidate_id)
            if evidence_item.candidate_id is not None
            else None
        )
        snapshots.append(
            {
                "source_type": evidence_item.source_type,
                "candidate_slug": candidate.slug if candidate is not None else "",
                "repo_full_name": (
                    candidate.repo_full_name
                    if candidate is not None
                    else evidence_item.payload.get("full_name", "")
                ),
            }
        )
    return snapshots


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
