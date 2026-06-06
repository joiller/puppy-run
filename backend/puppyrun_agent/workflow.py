from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_agent.catalog import select_candidates
from puppyrun_agent.clarification import build_initial_context, update_context_with_answer
from puppyrun_agent.criteria import CriterionProfile, generate_criteria
from puppyrun_agent.github_client import GitHubClient, RepositorySummary
from puppyrun_agent.phase2 import (
    apply_phase2_constraints,
    apply_phase2_criteria,
    build_adr,
    build_gap_analysis,
    build_phase2_candidates,
    build_research_plan,
    build_score_cells,
    normalize_phase2_draft,
)
from puppyrun_agent.recommendation import (
    build_recommendation,
    build_weighted_recommendation,
    score_candidate,
)
from puppyrun_api.config import get_settings
from puppyrun_api.models import (
    AgentEvent,
    AgentRun,
    AgentRunStatus,
    DecisionCandidate,
    DecisionCriterion,
    DecisionSession,
    DecisionSessionStatus,
    DecisionVersion,
    DecisionVersionStatus,
    EvidenceItem,
    Recommendation,
    ScoreCell,
    utc_now,
)
from puppyrun_api.repositories import workspace as workspace_repo


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
    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="phase1_started",
            message="Phase 1 workflow started",
        )
    )
    await db.commit()

    try:
        return await _run_phase1_steps(db, run, session, github_transport)
    except Exception as exc:
        await _mark_phase1_failed(db, run_id, exc)
        raise


async def _run_phase1_steps(
    db: AsyncSession,
    run: AgentRun,
    session: DecisionSession,
    github_transport: httpx.AsyncBaseTransport | None,
) -> str:
    version = await _create_next_version(
        db,
        session.id,
        label="Phase 1 baseline",
        source_version_id=None,
        change_summary={"kind": "phase1_baseline"},
    )

    await db.execute(
        delete(Recommendation)
        .where(Recommendation.session_id == session.id)
        .where(Recommendation.decision_version_id.is_(None))
    )
    await db.execute(
        delete(EvidenceItem)
        .where(EvidenceItem.session_id == session.id)
        .where(EvidenceItem.decision_version_id.is_(None))
    )
    await db.execute(
        delete(DecisionCriterion)
        .where(DecisionCriterion.session_id == session.id)
        .where(DecisionCriterion.decision_version_id.is_(None))
    )
    await db.execute(
        delete(DecisionCandidate)
        .where(DecisionCandidate.session_id == session.id)
        .where(DecisionCandidate.decision_version_id.is_(None))
    )

    context = _build_workflow_context(session)
    candidates = select_candidates(context)
    criteria = generate_criteria(context)

    criterion_models = [
        DecisionCriterion(
            session_id=session.id,
            decision_version_id=version.id,
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
    candidate_models_by_slug: dict[str, DecisionCandidate] = {}
    async with GitHubClient(
        api_base_url=settings.github_api_base_url,
        token=settings.github_token,
        transport=github_transport,
    ) as github:
        for candidate in candidates:
            repo = await github.fetch_repository_summary(candidate.repo_full_name)
            candidate_score = score_candidate(candidate, repo, context)
            health_summary = (
                f"{repo.full_name}: {repo.stars} stars, {repo.forks} forks, "
                f"{repo.open_issues} open issues, last pushed at {repo.pushed_at}."
            )
            candidate_model = DecisionCandidate(
                session_id=session.id,
                decision_version_id=version.id,
                name=candidate.name,
                slug=candidate.slug,
                repo_full_name=candidate.repo_full_name,
                include_reason=candidate.include_reason,
                health_summary=health_summary,
                health_metrics=repo.to_evidence_payload(),
                score=candidate_score.total,
            )
            db.add(candidate_model)
            await db.flush()
            candidate_models_by_slug[candidate.slug] = candidate_model
            db.add(
                EvidenceItem(
                    session_id=session.id,
                    decision_version_id=version.id,
                    candidate_id=candidate_model.id,
                    criterion_id=None,
                    source_type="github_repo",
                    source_url=repo.source_url,
                    title=f"GitHub repository health for {candidate.name}",
                    summary=health_summary,
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
    winner_model = candidate_models_by_slug[rationale["recommended_slug"]]
    db.add(
        Recommendation(
            session_id=session.id,
            decision_version_id=version.id,
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
    version.status = DecisionVersionStatus.completed
    version.completed_at = utc_now()
    version.adr = _phase1_adr(version.version_number, summary, rationale)
    session.status = DecisionSessionStatus.completed
    session.workflow_stage = "completed"
    session.current_summary = summary
    await db.commit()
    return summary


async def run_phase2_workflow(
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

    version = await _queued_phase2_version_for_run(db, run)
    draft = _phase2_draft_for_run(session, version)
    source_version = await _source_version_for_draft(db, session.id, draft, version)

    run.status = AgentRunStatus.running
    version.status = DecisionVersionStatus.running
    session.status = DecisionSessionStatus.running
    session.workflow_stage = "researching"
    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="phase2_started",
            message=f"Phase 2 workflow started for version {version.version_number}",
            payload={
                "version_id": str(version.id),
                "source_version_id": str(source_version.id),
            },
        )
    )
    await db.commit()

    try:
        return await _run_phase2_steps(
            db,
            run,
            session,
            version,
            source_version,
            draft,
            github_transport,
        )
    except Exception as exc:
        await _mark_phase2_failed(db, run_id, version.id, exc)
        raise


async def _run_phase2_steps(
    db: AsyncSession,
    run: AgentRun,
    session: DecisionSession,
    version: DecisionVersion,
    source_version: DecisionVersion,
    draft: dict,
    github_transport: httpx.AsyncBaseTransport | None,
) -> str:
    source_workspace = await workspace_repo.get_workspace(
        db,
        session.id,
        version_id=source_version.id,
    )
    baseline_context = {
        **(session.decision_context or {}),
        "candidates": _candidate_snapshots(source_workspace.candidates),
    }
    effective_context = apply_phase2_constraints(baseline_context, draft)
    candidates = build_phase2_candidates(baseline_context, draft)
    criteria = apply_phase2_criteria(
        _criterion_snapshots(source_workspace.criteria),
        draft,
        effective_context,
    )
    previous_evidence = _evidence_snapshots(
        source_workspace.evidence_items,
        source_workspace.candidates,
    )
    research_plan = build_research_plan(candidates, previous_evidence)
    gap_analysis = build_gap_analysis(draft, candidates, criteria, previous_evidence)

    version.gap_analysis = gap_analysis
    version.change_summary = {
        **dict(version.change_summary or {}),
        "source_version_id": str(source_version.id),
        "changed_candidates": gap_analysis["changed_candidates"],
        "changed_constraints": gap_analysis["changed_constraints"],
        "changed_weights": gap_analysis["changed_weights"],
    }
    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="targeted_research_planned",
            message=(
                f"Planned {len(research_plan['research_tasks'])} GitHub fetches "
                f"and {len(research_plan['reuse_tasks'])} evidence reuses"
            ),
            payload=research_plan,
        )
    )
    await db.commit()

    source_evidence = _source_github_evidence_maps(
        source_workspace.evidence_items,
        source_workspace.candidates,
    )
    repos = _reused_repositories(candidates, source_evidence)
    fetched_slugs = await _fetch_phase2_repositories(
        db,
        run,
        research_plan["research_tasks"],
        repos,
        github_transport,
    )
    return await _persist_phase2_success(
        db,
        run,
        session,
        version,
        effective_context,
        candidates,
        criteria,
        gap_analysis,
        repos,
        source_evidence,
        fetched_slugs,
    )


async def _fetch_phase2_repositories(
    db: AsyncSession,
    run: AgentRun,
    research_tasks: list[dict],
    repos: dict[str, RepositorySummary],
    github_transport: httpx.AsyncBaseTransport | None,
) -> set[str]:
    fetched_slugs: set[str] = set()
    settings = get_settings()
    async with GitHubClient(
        api_base_url=settings.github_api_base_url,
        token=settings.github_token,
        transport=github_transport,
    ) as github:
        for task in research_tasks:
            slug = _normalize_slug(task["candidate_slug"])
            repo = await github.fetch_repository_summary(task["repo_full_name"])
            repos[slug] = repo
            repos[repo.full_name] = repo
            fetched_slugs.add(slug)
            db.add(
                AgentEvent(
                    run_id=run.id,
                    event_type="github_repo_analyzed",
                    message=f"Analyzed {repo.full_name}",
                    payload=repo.to_evidence_payload(),
                )
            )
    return fetched_slugs


async def _persist_phase2_success(
    db: AsyncSession,
    run: AgentRun,
    session: DecisionSession,
    version: DecisionVersion,
    effective_context: dict,
    candidates: list[dict],
    criteria: list[dict],
    gap_analysis: dict,
    repos: dict[str, RepositorySummary],
    source_evidence: dict[str, dict[str, EvidenceItem]],
    fetched_slugs: set[str],
) -> str:
    candidate_models_by_slug: dict[str, DecisionCandidate] = {}
    for candidate in candidates:
        candidate_model = DecisionCandidate(
            session_id=session.id,
            decision_version_id=version.id,
            name=_clean_text(candidate["name"]),
            slug=_normalize_slug(candidate["slug"]),
            repo_full_name=_clean_text(candidate["repo_full_name"]),
            include_reason=_clean_text(candidate["include_reason"]),
            selection_state=_clean_text(candidate.get("selection_state", "included")),
            is_locked=bool(candidate.get("is_locked", False)),
        )
        db.add(candidate_model)
        await db.flush()
        candidate_models_by_slug[candidate_model.slug] = candidate_model

    criterion_models_by_name: dict[str, DecisionCriterion] = {}
    for criterion in criteria:
        criterion_model = DecisionCriterion(
            session_id=session.id,
            decision_version_id=version.id,
            name=_clean_text(criterion["name"]),
            weight=int(criterion["weight"]),
            rationale=_clean_text(criterion["rationale"]),
            evidence_needed=_clean_text(criterion["evidence_needed"]),
            is_locked=bool(criterion.get("is_locked", False)),
        )
        db.add(criterion_model)
        await db.flush()
        criterion_models_by_name[criterion_model.name] = criterion_model

    evidence_refs_by_candidate: dict[str, list[dict]] = {}
    evidence_id_by_source_url: dict[str, str] = {}
    for candidate in candidates:
        candidate_slug = _normalize_slug(candidate["slug"])
        candidate_model = candidate_models_by_slug[candidate_slug]
        evidence = _source_evidence_for_candidate(candidate, source_evidence)
        repo = _repo_for_candidate(candidate, repos)
        if candidate_slug in fetched_slugs or evidence is None:
            if repo is None:
                continue
            evidence_model = _new_github_evidence(
                session.id,
                version.id,
                candidate_model.id,
                candidate["name"],
                repo,
            )
        else:
            evidence_model = _copy_github_evidence(
                evidence,
                session.id,
                version.id,
                candidate_model.id,
            )
        db.add(evidence_model)
        await db.flush()
        evidence_refs_by_candidate[candidate_slug] = [
            {
                "source_type": evidence_model.source_type,
                "label": evidence_model.title,
                "source_url": evidence_model.source_url,
            }
        ]
        evidence_id_by_source_url[evidence_model.source_url] = str(evidence_model.id)

    criteria_profiles = [_criterion_profile(criterion) for criterion in criteria]
    score_cells = build_score_cells(
        candidates,
        criteria_profiles,
        repos,
        evidence_refs_by_candidate,
        context=effective_context,
    )
    for cell in score_cells:
        candidate_model = candidate_models_by_slug[_normalize_slug(cell["candidate_slug"])]
        criterion_model = criterion_models_by_name[_clean_text(cell["criterion_name"])]
        db.add(
            ScoreCell(
                session_id=session.id,
                decision_version_id=version.id,
                candidate_id=candidate_model.id,
                criterion_id=criterion_model.id,
                score=int(cell["score"]),
                status=_clean_text(cell["status"]),
                explanation=_clean_text(cell["explanation"]),
                evidence_item_ids=[
                    evidence_id_by_source_url[ref["source_url"]]
                    for ref in cell["evidence_refs"]
                    if ref.get("source_url") in evidence_id_by_source_url
                ],
            )
        )

    summary, rationale = build_weighted_recommendation(
        candidates,
        criteria_profiles,
        repos,
        effective_context,
        version_number=version.version_number,
    )
    scores_by_slug = {
        _normalize_slug(candidate["slug"]): int(candidate["weighted_score"])
        for candidate in rationale["ranked_candidates"]
    }
    for slug, score in scores_by_slug.items():
        candidate_models_by_slug[slug].score = score

    winner_model = candidate_models_by_slug[_normalize_slug(rationale["recommended_slug"])]
    db.add(
        Recommendation(
            session_id=session.id,
            decision_version_id=version.id,
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
    adr = build_adr(
        version.version_number,
        summary,
        rationale,
        gap_analysis,
        score_cells,
    )
    version.adr = f"{adr['title']}\n\n{adr['body']}"
    version.status = DecisionVersionStatus.completed
    version.completed_at = utc_now()
    run.status = AgentRunStatus.completed
    session.status = DecisionSessionStatus.completed
    session.workflow_stage = "completed"
    session.current_summary = summary
    context = dict(session.decision_context or {})
    context.pop("phase2_draft", None)
    context["phase2_gap_analysis"] = gap_analysis
    session.decision_context = context
    await db.commit()
    return summary


async def _mark_phase1_failed(db: AsyncSession, run_id: UUID, exc: Exception) -> None:
    await db.rollback()

    run = await db.get(AgentRun, run_id)
    if run is None:
        return

    session = await db.get(DecisionSession, run.session_id)
    run.status = AgentRunStatus.failed
    if session is not None:
        session.status = DecisionSessionStatus.failed
        session.workflow_stage = "failed"

    message = _failure_message(exc)
    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="phase1_failed",
            message=f"Phase 1 workflow failed: {message}",
            payload={
                "error": message,
                "error_type": type(exc).__name__,
            },
        )
    )
    await db.commit()


async def _mark_phase2_failed(
    db: AsyncSession,
    run_id: UUID,
    version_id: UUID,
    exc: Exception,
) -> None:
    await db.rollback()

    run = await db.get(AgentRun, run_id)
    version = await db.get(DecisionVersion, version_id)
    if run is None or version is None:
        return

    session = await db.get(DecisionSession, run.session_id)
    run.status = AgentRunStatus.failed
    version.status = DecisionVersionStatus.failed
    message = _failure_message(exc)
    version.gap_analysis = {
        **dict(version.gap_analysis or {}),
        "failure": {
            "error": message,
            "error_type": type(exc).__name__,
        },
    }
    if version.adr is None:
        version.adr = f"Phase 2 failed before ADR generation: {message}"
    if session is not None:
        session.status = DecisionSessionStatus.failed
        session.workflow_stage = "failed"

    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="phase2_failed",
            message=f"Phase 2 workflow failed: {message}",
            payload={
                "version_id": str(version.id),
                "error": message,
                "error_type": type(exc).__name__,
            },
        )
    )
    await db.commit()


async def _create_next_version(
    db: AsyncSession,
    session_id: UUID,
    *,
    label: str,
    source_version_id: UUID | None,
    change_summary: dict,
) -> DecisionVersion:
    next_number = await _next_version_number(db, session_id)
    version_label = label if next_number == 1 else f"{label} rerun"
    version = DecisionVersion(
        session_id=session_id,
        version_number=next_number,
        label=version_label,
        status=DecisionVersionStatus.running,
        source_version_id=source_version_id,
        change_summary=change_summary,
        gap_analysis={"items": []},
    )
    db.add(version)
    await db.flush()
    return version


async def _next_version_number(db: AsyncSession, session_id: UUID) -> int:
    current_max = await db.scalar(
        select(func.max(DecisionVersion.version_number)).where(
            DecisionVersion.session_id == session_id
        )
    )
    return int(current_max or 0) + 1


async def _queued_phase2_version_for_run(
    db: AsyncSession,
    run: AgentRun,
) -> DecisionVersion:
    result = await db.execute(
        select(DecisionVersion)
        .where(DecisionVersion.session_id == run.session_id)
        .where(DecisionVersion.status == DecisionVersionStatus.queued)
        .order_by(DecisionVersion.created_at.asc(), DecisionVersion.id.asc())
    )
    versions = list(result.scalars())
    for version in versions:
        if str((version.change_summary or {}).get("agent_run_id")) == str(run.id):
            return version
    if len(versions) == 1:
        return versions[0]
    raise ValueError(f"queued decision version not found for run: {run.id}")


def _phase2_draft_for_run(session: DecisionSession, version: DecisionVersion) -> dict:
    context = session.decision_context or {}
    raw_draft = context.get("phase2_draft")
    if not isinstance(raw_draft, dict):
        raise ValueError("no phase2 draft found")
    draft = normalize_phase2_draft(raw_draft, version.source_version_id)
    if not _draft_has_changes(draft):
        raise ValueError("no phase2 draft changes found")
    return draft


async def _source_version_for_draft(
    db: AsyncSession,
    session_id: UUID,
    draft: dict,
    version: DecisionVersion,
) -> DecisionVersion:
    source_version_id = draft.get("source_version_id") or version.source_version_id
    if source_version_id is None:
        raise ValueError("no source version found")
    source_version = await db.get(DecisionVersion, UUID(str(source_version_id)))
    if (
        source_version is None
        or source_version.session_id != session_id
        or source_version.status != DecisionVersionStatus.completed
    ):
        raise ValueError("no completed source version found")
    return source_version


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
                    else _clean_text(evidence_item.payload.get("full_name"))
                ),
            }
        )
    return snapshots


def _source_github_evidence_maps(
    evidence_items: list[EvidenceItem],
    candidates: list[DecisionCandidate],
) -> dict[str, dict[str, EvidenceItem]]:
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    by_slug: dict[str, EvidenceItem] = {}
    by_repo: dict[str, EvidenceItem] = {}
    for evidence in evidence_items:
        if evidence.source_type != "github_repo":
            continue
        candidate = (
            candidates_by_id.get(evidence.candidate_id)
            if evidence.candidate_id is not None
            else None
        )
        if candidate is not None:
            by_slug[_normalize_slug(candidate.slug)] = evidence
            by_repo[candidate.repo_full_name] = evidence
            continue
        repo_full_name = _clean_text(evidence.payload.get("full_name"))
        if repo_full_name:
            by_repo[repo_full_name] = evidence
    return {"by_slug": by_slug, "by_repo": by_repo}


def _reused_repositories(
    candidates: list[dict],
    source_evidence: dict[str, dict[str, EvidenceItem]],
) -> dict[str, RepositorySummary]:
    repos: dict[str, RepositorySummary] = {}
    for candidate in candidates:
        candidate_slug = _normalize_slug(candidate["slug"])
        evidence = _source_evidence_for_candidate(candidate, source_evidence)
        if evidence is None:
            continue
        repo = _repository_summary_from_evidence(evidence, candidate)
        repos[candidate_slug] = repo
        repos[repo.full_name] = repo
    return repos


def _source_evidence_for_candidate(
    candidate: dict,
    source_evidence: dict[str, dict[str, EvidenceItem]],
) -> EvidenceItem | None:
    candidate_slug = _normalize_slug(candidate["slug"])
    repo_full_name = _clean_text(candidate["repo_full_name"])
    return source_evidence["by_slug"].get(candidate_slug) or source_evidence["by_repo"].get(
        repo_full_name
    )


def _repo_for_candidate(
    candidate: dict,
    repos: dict[str, RepositorySummary],
) -> RepositorySummary | None:
    candidate_slug = _normalize_slug(candidate["slug"])
    repo_full_name = _clean_text(candidate["repo_full_name"])
    return repos.get(candidate_slug) or repos.get(repo_full_name)


def _repository_summary_from_evidence(
    evidence: EvidenceItem,
    candidate: dict,
) -> RepositorySummary:
    payload = dict(evidence.payload or {})
    full_name = _clean_text(payload.get("full_name")) or _clean_text(
        candidate["repo_full_name"]
    )
    return RepositorySummary(
        full_name=full_name,
        source_url=evidence.source_url,
        description=_clean_text(payload.get("description")),
        stars=int(payload.get("stars") or 0),
        forks=int(payload.get("forks") or 0),
        open_issues=int(payload.get("open_issues") or 0),
        pushed_at=_clean_text(payload.get("pushed_at")),
        license_spdx_id=payload.get("license_spdx_id"),
    )


def _new_github_evidence(
    session_id: UUID,
    version_id: UUID,
    candidate_id: UUID,
    candidate_name: str,
    repo: RepositorySummary,
) -> EvidenceItem:
    health_summary = (
        f"{repo.full_name}: {repo.stars} stars, {repo.forks} forks, "
        f"{repo.open_issues} open issues, last pushed at {repo.pushed_at}."
    )
    return EvidenceItem(
        session_id=session_id,
        decision_version_id=version_id,
        candidate_id=candidate_id,
        criterion_id=None,
        source_type="github_repo",
        source_url=repo.source_url,
        title=f"GitHub repository health for {_clean_text(candidate_name)}",
        summary=health_summary,
        credibility="medium",
        payload=repo.to_evidence_payload(),
    )


def _copy_github_evidence(
    evidence: EvidenceItem,
    session_id: UUID,
    version_id: UUID,
    candidate_id: UUID,
) -> EvidenceItem:
    return EvidenceItem(
        session_id=session_id,
        decision_version_id=version_id,
        candidate_id=candidate_id,
        criterion_id=None,
        source_type=evidence.source_type,
        source_url=evidence.source_url,
        title=evidence.title,
        summary=evidence.summary,
        credibility=evidence.credibility,
        payload=dict(evidence.payload or {}),
    )


def _criterion_profile(criterion: dict) -> CriterionProfile:
    return CriterionProfile(
        name=_clean_text(criterion["name"]),
        weight=int(criterion["weight"]),
        rationale=_clean_text(criterion["rationale"]),
        evidence_needed=_clean_text(criterion["evidence_needed"]),
        is_locked=bool(criterion.get("is_locked", False)),
        phase2_weight_reason=_clean_text(criterion.get("phase2_weight_reason")),
    )


def _phase1_adr(version_number: int, summary: str, rationale: dict) -> str:
    ranked = rationale.get("ranked_candidates", [])
    options = "\n".join(
        f"- {candidate['name']}: {candidate['score']}/100"
        for candidate in ranked
        if isinstance(candidate, dict)
    )
    return "\n\n".join(
        [
            f"ADR v{version_number}: Phase 1 baseline",
            f"## Decision\n{summary}",
            f"## Options\n{options or '- No ranked candidates were produced.'}",
        ]
    )


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_slug(value: Any) -> str:
    return _clean_text(value).lower()


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            payload = exc.response.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict) and isinstance(payload.get("message"), str):
            return payload["message"]
    return str(exc)


def _build_workflow_context(session: DecisionSession) -> dict:
    context = {
        **build_initial_context(session.prompt),
        **(session.decision_context or {}),
    }
    clarification = context.get("clarification")
    if not isinstance(clarification, dict):
        return context

    answer = clarification.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return context

    return update_context_with_answer(context, answer)
