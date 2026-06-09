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
from puppyrun_agent.phase3 import build_risk_verification_pipeline
from puppyrun_agent.recommendation import (
    CandidateScore,
    build_recommendation,
    build_weighted_recommendation,
    score_candidate,
)
from puppyrun_agent.source_adapters import EvidenceSourceResult
from puppyrun_agent.tool_runtime import (
    RegisteredTool,
    ToolContext,
    ToolResult,
    ToolRuntime,
    sanitize_error,
)
from puppyrun_api.config import get_settings
from puppyrun_api.models import (
    AgentEvent,
    AgentRun,
    AgentRunStatus,
    Claim,
    DecisionCandidate,
    DecisionCriterion,
    DecisionSession,
    DecisionSessionStatus,
    DecisionVersion,
    DecisionVersionStatus,
    EvidenceItem,
    Recommendation,
    RiskSignal,
    ScoreCell,
    VerificationTask,
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
    repos_by_slug: dict[str, RepositorySummary] = {}
    candidate_models_by_slug: dict[str, DecisionCandidate] = {}
    async with GitHubClient(
        api_base_url=settings.github_api_base_url,
        token=settings.github_token,
        transport=github_transport,
    ) as github:
        for candidate in candidates:
            repo = await github.fetch_repository_summary(candidate.repo_full_name)
            repos_by_slug[candidate.slug] = repo
            repos_by_slug[repo.full_name] = repo
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

    risk_score_data = await _run_phase3_steps(
        db,
        run,
        session.id,
        version,
        candidate_models_by_slug,
        repos_by_slug,
        previous_evidence=[],
        fail_on_provider_error=False,
    )
    scored = _apply_phase3_adjustments_to_scored(scored, risk_score_data)
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


async def _run_phase3_steps(
    db: AsyncSession,
    run: AgentRun,
    session_id: UUID,
    version: DecisionVersion,
    candidate_models_by_slug: dict[str, DecisionCandidate],
    repos: dict[str, RepositorySummary],
    *,
    previous_evidence: list[EvidenceItem],
    fail_on_provider_error: bool,
) -> dict[str, dict]:
    source_counts = {
        "github_issue": len(candidate_models_by_slug),
        "tavily_search": 1,
        "reddit": 1,
    }
    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="phase3_sources_planned",
            message=f"Planned Phase 3 source checks for {len(candidate_models_by_slug)} candidates",
            payload={
                "candidate_count": len(candidate_models_by_slug),
                "source_types": list(source_counts),
                "source_counts": source_counts,
            },
        )
    )
    runtime = ToolRuntime(
        db,
        session_id=session_id,
        decision_version_id=version.id,
    )
    runtime.register(RegisteredTool("phase3_candidate_sources", _phase3_candidate_source_handler))
    runtime.register(RegisteredTool("phase3_tavily_search", _phase3_tavily_search_handler))
    runtime.register(RegisteredTool("phase3_reddit_search", _phase3_reddit_search_handler))

    source_results: list[dict] = []
    for slug, candidate in candidate_models_by_slug.items():
        repo = repos.get(slug) or repos.get(candidate.repo_full_name)
        if repo is None:
            continue
        result = await runtime.execute(
            "phase3_candidate_sources",
            {
                "candidate_slug": candidate.slug,
                "candidate_name": candidate.name,
                "repo": _repo_to_phase3_payload(repo),
            },
            idempotency_parts={"candidate_slug": candidate.slug, "version": str(version.id)},
        )
        if result.status == "completed":
            source_results.extend(_phase3_source_results_from_tool_result(result))

    await runtime.execute(
        "phase3_tavily_search",
        {"query": "agent framework maintenance risk"},
        idempotency_parts={"version": str(version.id)},
    )
    await runtime.execute(
        "phase3_reddit_search",
        {"query": "agent framework maintenance risk"},
        idempotency_parts={"version": str(version.id)},
    )

    evidence_models = await _persist_phase3_evidence(
        db,
        session_id,
        version.id,
        candidate_models_by_slug,
        source_results,
        previous_evidence,
    )
    try:
        phase3_result = build_risk_verification_pipeline(source_results)
    except Exception as exc:
        error = sanitize_error(str(exc))
        if fail_on_provider_error:
            raise RuntimeError(error) from exc
        version.gap_analysis = {
            **dict(version.gap_analysis or {}),
            "phase3_failure": {"error": error},
            "risk_adjusted_scores": _risk_adjusted_score_data(
                candidate_models_by_slug,
                {},
            ),
        }
        db.add(
            AgentEvent(
                run_id=run.id,
                event_type="risk_verification_completed",
                message="Phase 3 risk verification skipped after provider failure",
                payload={"error": error},
            )
        )
        await db.flush()
        return version.gap_analysis["risk_adjusted_scores"]

    claim_models = await _persist_phase3_claims(
        db,
        session_id,
        version.id,
        candidate_models_by_slug,
        evidence_models,
        phase3_result["claims"],
    )
    risk_models, task_models = await _persist_phase3_risks_and_tasks(
        db,
        session_id,
        version.id,
        candidate_models_by_slug,
        claim_models,
        phase3_result["risk_signals"],
        phase3_result["verification_tasks"],
    )
    risk_score_data = _risk_adjusted_score_data(
        candidate_models_by_slug,
        phase3_result["risk_adjustments"],
    )
    _apply_phase3_adjustments_to_candidate_models(candidate_models_by_slug, risk_score_data)
    version.gap_analysis = {
        **dict(version.gap_analysis or {}),
        "risk_adjusted_scores": risk_score_data,
    }
    db.add_all(
        [
            AgentEvent(
                run_id=run.id,
                event_type="claims_extracted",
                message=f"Extracted {len(claim_models)} Phase 3 claims",
                payload={"claim_count": len(claim_models)},
            ),
            AgentEvent(
                run_id=run.id,
                event_type="risks_clustered",
                message=f"Clustered {len(risk_models)} Phase 3 risks",
                payload={"risk_count": len(risk_models)},
            ),
            AgentEvent(
                run_id=run.id,
                event_type="verification_tasks_created",
                message=f"Created {len(task_models)} Phase 3 verification tasks",
                payload={"verification_task_count": len(task_models)},
            ),
            AgentEvent(
                run_id=run.id,
                event_type="risk_verification_completed",
                message="Completed Phase 3 risk verification",
                payload={
                    "confirmed_risk_count": sum(
                        1 for risk in risk_models if risk.status == "confirmed"
                    )
                },
            ),
            AgentEvent(
                run_id=run.id,
                event_type="risk_adjusted_scores",
                message="Applied Phase 3 risk score adjustments",
                payload={"risk_adjusted_scores": risk_score_data},
            ),
        ]
    )
    await db.flush()
    return risk_score_data


async def _phase3_candidate_source_handler(
    _context: ToolContext,
    inputs: dict,
) -> ToolResult:
    repo = dict(inputs.get("repo") or {})
    candidate_slug = _clean_text(inputs.get("candidate_slug"))
    candidate_name = _clean_text(inputs.get("candidate_name")) or candidate_slug
    repo_full_name = _clean_text(repo.get("full_name"))
    source_url = _clean_text(repo.get("source_url")) or f"https://github.com/{repo_full_name}"
    open_issues = int(repo.get("open_issues") or 0)
    severity_text = "critical " if open_issues >= 100 else ""
    result = EvidenceSourceResult(
        source_type="github_issue",
        source_url=f"{source_url}/issues",
        title=f"{candidate_name} maintenance risk signals",
        summary=(
            f"{repo_full_name} has {open_issues} open issues, indicating "
            f"{severity_text}maintenance risk that should be verified."
        ),
        citation_text=f"{open_issues} open GitHub issues for {repo_full_name}.",
        credibility="medium",
        candidate_slug=candidate_slug,
        metadata={
            "full_name": repo_full_name,
            "repo_full_name": repo_full_name,
            "open_issues": open_issues,
            "stars": int(repo.get("stars") or 0),
            "source_profile": "github_issue",
            "source_profile_query": f"{repo_full_name} open issues maintenance risk",
        },
    )
    return ToolResult(
        status="completed",
        source_type="github_issue",
        source_url=result.source_url,
        request_summary=f"Derived Phase 3 source evidence for {repo_full_name}.",
        response_summary=f"Prepared 1 Phase 3 source result for {candidate_slug}.",
        payload={"evidence_results": [_evidence_source_result_to_dict(result)]},
    )


async def _phase3_tavily_search_handler(
    _context: ToolContext,
    _inputs: dict,
) -> ToolResult:
    settings = get_settings()
    if not settings.tavily_api_key:
        return ToolResult(
            status="skipped",
            source_type="tavily_search",
            request_summary="Optional Tavily Phase 3 search",
            response_summary="Skipped optional Tavily search because no API key is configured.",
            payload={"reason": "missing_tavily_api_key"},
        )
    return ToolResult(
        status="skipped",
        source_type="tavily_search",
        request_summary="Optional Tavily Phase 3 search",
        response_summary="Live Tavily collection is deferred from local deterministic workflow.",
        payload={"reason": "deferred_live_search"},
    )


async def _phase3_reddit_search_handler(
    _context: ToolContext,
    _inputs: dict,
) -> ToolResult:
    settings = get_settings()
    if not settings.enable_reddit:
        return ToolResult(
            status="skipped",
            source_type="reddit",
            request_summary="Optional Reddit Phase 3 search",
            response_summary="Skipped optional Reddit search because it is disabled.",
            payload={"reason": "reddit_disabled"},
        )
    return ToolResult(
        status="skipped",
        source_type="reddit",
        request_summary="Optional Reddit Phase 3 search",
        response_summary="Live Reddit collection is deferred from local deterministic workflow.",
        payload={"reason": "deferred_live_search"},
    )


def _phase3_source_results_from_tool_result(result: ToolResult) -> list[dict]:
    payload = dict(result.payload or {})
    rows = payload.get("evidence_results")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


async def _persist_phase3_evidence(
    db: AsyncSession,
    session_id: UUID,
    version_id: UUID,
    candidate_models_by_slug: dict[str, DecisionCandidate],
    source_results: list[dict],
    previous_evidence: list[EvidenceItem],
) -> list[EvidenceItem]:
    reuse_map = _phase3_reuse_map(previous_evidence)
    evidence_models = []
    for result in source_results:
        candidate_slug = _normalize_slug(result.get("candidate_slug"))
        candidate_model = candidate_models_by_slug.get(candidate_slug)
        if candidate_model is None:
            continue
        content_hash = _clean_text(result.get("content_hash"))
        metadata = dict(result.get("metadata") or {})
        repo_full_name = (
            _clean_text(metadata.get("repo_full_name"))
            or _clean_text(metadata.get("full_name"))
            or candidate_model.repo_full_name
        )
        source_profile_query = _clean_text(metadata.get("source_profile_query"))
        payload = {
            "phase3": True,
            "candidate_slug": candidate_slug,
            "content_hash": content_hash,
            "repo_full_name": repo_full_name,
            "full_name": repo_full_name,
            "source_profile_query": source_profile_query,
            "metadata": metadata,
        }
        reuse_key = (
            candidate_slug,
            repo_full_name,
            source_profile_query,
            _clean_text(result.get("source_type")),
            _clean_text(result.get("source_url")),
            content_hash,
        )
        reused = reuse_map.get(reuse_key)
        if reused is not None:
            payload["reused_from_evidence_item_id"] = str(reused.id)
            payload["reused_from_version_id"] = str(reused.decision_version_id)
        evidence_model = EvidenceItem(
            session_id=session_id,
            decision_version_id=version_id,
            candidate_id=candidate_model.id,
            criterion_id=None,
            source_type=_clean_text(result.get("source_type")),
            source_url=_clean_text(result.get("source_url")),
            title=_clean_text(result.get("title")),
            summary=_clean_text(result.get("summary")),
            credibility=_clean_text(result.get("credibility")) or "medium",
            payload=payload,
        )
        db.add(evidence_model)
        await db.flush()
        evidence_models.append(evidence_model)
    return evidence_models


async def _persist_phase3_claims(
    db: AsyncSession,
    session_id: UUID,
    version_id: UUID,
    candidate_models_by_slug: dict[str, DecisionCandidate],
    evidence_models: list[EvidenceItem],
    claims: list[dict],
) -> list[Claim]:
    claim_models = []
    for claim in claims:
        candidate_model = candidate_models_by_slug.get(_normalize_slug(claim.get("candidate_slug")))
        if candidate_model is None:
            continue
        source_index = claim.get("source_evidence_index")
        source_evidence = (
            evidence_models[source_index]
            if isinstance(source_index, int) and 0 <= source_index < len(evidence_models)
            else None
        )
        claim_model = Claim(
            session_id=session_id,
            decision_version_id=version_id,
            candidate_id=candidate_model.id,
            criterion_id=None,
            source_evidence_item_id=source_evidence.id if source_evidence is not None else None,
            source_type=_clean_text(claim.get("source_type")),
            source_url=_clean_text(claim.get("source_url")),
            title=_clean_text(claim.get("title")),
            summary=_clean_text(claim.get("summary")),
            citation_text=_clean_text(claim.get("citation_text")),
            credibility=_clean_text(claim.get("credibility")) or "medium",
            confidence=int(claim.get("confidence") or 0),
            content_hash=_clean_text(claim.get("content_hash")),
            payload={
                "risk_key": claim.get("risk_key"),
                "source_content_hash": claim.get("source_content_hash"),
            },
        )
        db.add(claim_model)
        await db.flush()
        claim_models.append(claim_model)
    return claim_models


async def _persist_phase3_risks_and_tasks(
    db: AsyncSession,
    session_id: UUID,
    version_id: UUID,
    candidate_models_by_slug: dict[str, DecisionCandidate],
    claim_models: list[Claim],
    risks: list[dict],
    verification_tasks: list[dict],
) -> tuple[list[RiskSignal], list[VerificationTask]]:
    risk_models = []
    task_models = []
    for risk in risks:
        candidate_model = candidate_models_by_slug.get(_normalize_slug(risk.get("candidate_slug")))
        if candidate_model is None:
            continue
        supporting_claim_ids = [
            str(claim_models[index].id)
            for index in risk.get("supporting_claim_indexes", [])
            if isinstance(index, int) and 0 <= index < len(claim_models)
        ]
        risk_model = RiskSignal(
            session_id=session_id,
            decision_version_id=version_id,
            candidate_id=candidate_model.id,
            risk_key=_clean_text(risk.get("risk_key")),
            title=_clean_text(risk.get("title")),
            summary=_clean_text(risk.get("summary")),
            severity=_clean_text(risk.get("severity")) or "low",
            status=_clean_text(risk.get("status")) or "unverified",
            credibility=_clean_text(risk.get("credibility")) or "low",
            score_impact=int(risk.get("score_impact") or 0),
            supporting_claim_ids=supporting_claim_ids,
            verification_task_ids=[],
            payload=dict(risk.get("payload") or {}),
        )
        db.add(risk_model)
        await db.flush()
        task_index = (risk.get("payload") or {}).get("verification_task_index")
        task_payload = (
            verification_tasks[task_index]
            if isinstance(task_index, int) and 0 <= task_index < len(verification_tasks)
            else None
        )
        if task_payload is not None:
            task_model = VerificationTask(
                session_id=session_id,
                decision_version_id=version_id,
                candidate_id=candidate_model.id,
                risk_signal_id=risk_model.id,
                status=_clean_text(task_payload.get("status")) or "planned",
                verification_question=_clean_text(task_payload.get("verification_question")),
                stronger_source_type=_clean_text(task_payload.get("stronger_source_type")) or None,
                stronger_source_url=_clean_text(task_payload.get("stronger_source_url")) or None,
                verdict=_clean_text(task_payload.get("verdict")) or None,
                rationale=_clean_text(task_payload.get("rationale")) or None,
                payload=dict(task_payload.get("payload") or {}),
            )
            db.add(task_model)
            await db.flush()
            risk_model.verification_task_ids = [str(task_model.id)]
            task_models.append(task_model)
        risk_models.append(risk_model)
    return risk_models, task_models


async def _phase3_presentation_snapshot(db: AsyncSession, version_id: UUID) -> dict:
    risk_rows = list(
        (
            await db.execute(
                select(RiskSignal)
                .where(RiskSignal.decision_version_id == version_id)
                .order_by(RiskSignal.created_at.asc(), RiskSignal.id.asc())
            )
        ).scalars()
    )
    task_rows = list(
        (
            await db.execute(
                select(VerificationTask)
                .where(VerificationTask.decision_version_id == version_id)
                .order_by(VerificationTask.created_at.asc(), VerificationTask.id.asc())
            )
        ).scalars()
    )
    candidate_ids = {risk.candidate_id for risk in risk_rows}
    candidates = list(
        (
            await db.execute(
                select(DecisionCandidate).where(DecisionCandidate.id.in_(candidate_ids))
            )
        ).scalars()
    ) if candidate_ids else []
    candidate_slug_by_id = {str(candidate.id): candidate.slug for candidate in candidates}
    task_by_risk_id = {str(task.risk_signal_id): task for task in task_rows}
    risk_signals = []
    for risk in risk_rows:
        task = task_by_risk_id.get(str(risk.id))
        payload = dict(risk.payload or {})
        if task is not None:
            payload["verification_rationale"] = task.rationale
            payload["stronger_source_type"] = task.stronger_source_type
            payload["stronger_source_url"] = task.stronger_source_url
        risk_signals.append(
            {
                "candidate_slug": candidate_slug_by_id.get(str(risk.candidate_id), ""),
                "risk_key": risk.risk_key,
                "title": risk.title,
                "summary": risk.summary,
                "severity": risk.severity,
                "status": risk.status,
                "credibility": risk.credibility,
                "score_impact": risk.score_impact,
                "payload": payload,
            }
        )
    return {"phase3_risk_signals": risk_signals}


def _phase3_reuse_map(
    previous_evidence: list[EvidenceItem],
) -> dict[tuple[str, str, str, str, str, str], EvidenceItem]:
    reuse_map = {}
    for evidence in previous_evidence:
        payload = dict(evidence.payload or {})
        if not payload.get("phase3"):
            continue
        candidate_slug = _normalize_slug(payload.get("candidate_slug"))
        repo_full_name = _clean_text(payload.get("repo_full_name") or payload.get("full_name"))
        source_profile_query = _clean_text(payload.get("source_profile_query"))
        content_hash = _clean_text(payload.get("content_hash"))
        if candidate_slug and repo_full_name and source_profile_query and content_hash:
            reuse_map[
                (
                    candidate_slug,
                    repo_full_name,
                    source_profile_query,
                    evidence.source_type,
                    evidence.source_url,
                    content_hash,
                )
            ] = evidence
    return reuse_map


def _risk_adjusted_score_data(
    candidate_models_by_slug: dict[str, DecisionCandidate],
    risk_adjustments: dict[str, dict],
) -> dict[str, dict]:
    score_data = {}
    for slug, candidate in candidate_models_by_slug.items():
        base_score = int(candidate.score or 0)
        adjustment = dict(risk_adjustments.get(slug) or {})
        risk_adjustment = int(adjustment.get("risk_adjustment") or 0)
        adjusted_score = max(0, base_score + risk_adjustment)
        score_data[slug] = {
            "base_score": base_score,
            "risk_adjustment": risk_adjustment,
            "uncapped_risk_adjustment": int(
                adjustment.get("uncapped_risk_adjustment") or risk_adjustment
            ),
            "adjusted_score": adjusted_score,
            "confirmed_risk_count": int(adjustment.get("confirmed_risk_count") or 0),
        }
    return score_data


def _apply_phase3_adjustments_to_candidate_models(
    candidate_models_by_slug: dict[str, DecisionCandidate],
    risk_score_data: dict[str, dict],
) -> None:
    for slug, payload in risk_score_data.items():
        candidate = candidate_models_by_slug.get(slug)
        if candidate is None:
            continue
        metrics = dict(candidate.health_metrics or {})
        metrics["phase3_risk_adjustment"] = payload
        candidate.health_metrics = metrics
        candidate.score = int(payload["adjusted_score"])


def _phase3_decision_context(context: dict, gap_analysis: dict | None) -> dict:
    phase3_context = dict(context)
    gap = dict(gap_analysis or {})
    if "phase3_risk_adjustments" in gap:
        phase3_context["phase3_risk_adjustments"] = gap["phase3_risk_adjustments"]
    elif "risk_adjusted_scores" in gap:
        phase3_context["phase3_risk_adjustments"] = gap["risk_adjusted_scores"]
    if "phase3_risk_signals" in gap:
        phase3_context["phase3_risk_signals"] = gap["phase3_risk_signals"]
    return phase3_context


def _apply_phase3_adjustments_to_scored(
    scored: list[tuple[Any, RepositorySummary, CandidateScore]],
    risk_score_data: dict[str, dict],
) -> list[tuple[Any, RepositorySummary, CandidateScore]]:
    adjusted = []
    for candidate, repo, candidate_score in scored:
        payload = risk_score_data.get(candidate.slug)
        if payload is None:
            adjusted.append((candidate, repo, candidate_score))
            continue
        adjusted.append(
            (
                candidate,
                repo,
                CandidateScore(
                    slug=candidate_score.slug,
                    total=int(payload["adjusted_score"]),
                    reasons=list(candidate_score.reasons),
                ),
            )
        )
    return adjusted


def _apply_phase3_adjustments_to_weighted_rationale(
    rationale: dict,
    risk_score_data: dict[str, dict],
) -> dict:
    ranked = []
    for candidate in rationale.get("ranked_candidates", []):
        row = dict(candidate)
        payload = risk_score_data.get(_normalize_slug(row.get("slug")))
        if payload is not None:
            row["base_score"] = payload["base_score"]
            row["risk_adjustment"] = payload["risk_adjustment"]
            row["uncapped_risk_adjustment"] = payload["uncapped_risk_adjustment"]
            row["score"] = payload["adjusted_score"]
            row["weighted_score"] = payload["adjusted_score"]
        ranked.append(row)
    ranked.sort(key=lambda item: item["weighted_score"], reverse=True)
    adjusted = dict(rationale)
    adjusted["ranked_candidates"] = ranked
    if ranked:
        adjusted["recommended_slug"] = ranked[0]["slug"]
        adjusted["recommended_repo"] = ranked[0]["repo"]
    return adjusted


def _repo_to_phase3_payload(repo: RepositorySummary) -> dict:
    return {
        **repo.to_evidence_payload(),
        "full_name": repo.full_name,
        "source_url": repo.source_url,
    }


def _evidence_source_result_to_dict(result: EvidenceSourceResult) -> dict:
    return {
        "source_type": result.source_type,
        "source_url": result.source_url,
        "title": result.title,
        "summary": result.summary,
        "citation_text": result.citation_text,
        "credibility": result.credibility,
        "candidate_slug": result.candidate_slug,
        "metadata": dict(result.metadata or {}),
        "content_hash": result.content_hash,
    }


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

    try:
        draft = _phase2_draft_for_run(version)
        source_version = await _source_version_for_draft(db, session.id, draft, version)

        run.status = AgentRunStatus.running
        version.status = DecisionVersionStatus.running
        if _current_phase2_draft_matches_version(dict(session.decision_context or {}), version):
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
        source_workspace.evidence_items,
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
    previous_evidence: list[EvidenceItem],
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
    _base_summary, base_rationale = build_weighted_recommendation(
        candidates,
        criteria_profiles,
        repos,
        effective_context,
        version_number=version.version_number,
    )
    base_scores_by_slug = {
        _normalize_slug(candidate["slug"]): int(candidate["weighted_score"])
        for candidate in base_rationale["ranked_candidates"]
    }
    for slug, score in base_scores_by_slug.items():
        candidate_models_by_slug[slug].score = score

    await _run_phase3_steps(
        db,
        run,
        session.id,
        version,
        candidate_models_by_slug,
        repos,
        previous_evidence=previous_evidence,
        fail_on_provider_error=True,
    )
    phase3_presentation = await _phase3_presentation_snapshot(db, version.id)
    presentation_gap_analysis = {
        **dict(version.gap_analysis or gap_analysis),
        **phase3_presentation,
    }
    risk_context = _phase3_decision_context(effective_context, presentation_gap_analysis)
    score_cells = build_score_cells(
        candidates,
        criteria_profiles,
        repos,
        evidence_refs_by_candidate,
        context=risk_context,
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
        risk_context,
        version_number=version.version_number,
    )
    adjusted_scores_by_slug = {
        _normalize_slug(candidate["slug"]): int(candidate["weighted_score"])
        for candidate in rationale["ranked_candidates"]
    }
    for slug, score in adjusted_scores_by_slug.items():
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
            event_type="recommendation_version_created",
            message=summary,
            payload=rationale,
        )
    )
    adr = build_adr(
        version.version_number,
        summary,
        rationale,
        presentation_gap_analysis,
        score_cells,
    )
    version.adr = f"{adr['title']}\n\n{adr['body']}"
    version.status = DecisionVersionStatus.completed
    version.completed_at = utc_now()
    run.status = AgentRunStatus.completed
    context = dict(session.decision_context or {})
    if _current_phase2_draft_matches_version(context, version):
        session.status = DecisionSessionStatus.completed
        session.workflow_stage = "completed"
        session.current_summary = summary
        context.pop("phase2_draft", None)
        context["phase2_gap_analysis"] = dict(version.gap_analysis or gap_analysis)
        session.decision_context = context
    else:
        await _release_stale_phase2_session_state(db, session, run.id)
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
        if _current_phase2_draft_matches_version(dict(session.decision_context or {}), version):
            session.status = DecisionSessionStatus.failed
            session.workflow_stage = "failed"
        else:
            await _release_stale_phase2_session_state(db, session, run.id)

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


def _phase2_draft_for_run(version: DecisionVersion) -> dict:
    change_summary = version.change_summary or {}
    raw_draft = change_summary.get("phase2_draft")
    if not isinstance(raw_draft, dict):
        raise ValueError("no phase2 draft snapshot found")
    draft = normalize_phase2_draft(raw_draft, version.source_version_id)
    if not _draft_has_changes(draft):
        raise ValueError("no phase2 draft changes found")
    return draft


def _current_phase2_draft_matches_version(context: dict, version: DecisionVersion) -> bool:
    current_draft = context.get("phase2_draft")
    if not isinstance(current_draft, dict):
        return True
    queued_draft = (version.change_summary or {}).get("phase2_draft")
    if not isinstance(queued_draft, dict):
        return False
    normalized_current = normalize_phase2_draft(current_draft, version.source_version_id)
    normalized_queued = normalize_phase2_draft(queued_draft, version.source_version_id)
    return normalized_current == normalized_queued


async def _release_stale_phase2_session_state(
    db: AsyncSession,
    session: DecisionSession,
    completed_run_id: UUID,
) -> None:
    if session.status not in {
        DecisionSessionStatus.queued,
        DecisionSessionStatus.running,
    }:
        return

    active_run_count = await db.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.session_id == session.id)
        .where(AgentRun.id != completed_run_id)
        .where(AgentRun.status.in_([AgentRunStatus.queued, AgentRunStatus.running]))
    )
    if int(active_run_count or 0) > 0:
        return

    completed_version_count = await db.scalar(
        select(func.count())
        .select_from(DecisionVersion)
        .where(DecisionVersion.session_id == session.id)
        .where(DecisionVersion.status == DecisionVersionStatus.completed)
    )
    session.status = (
        DecisionSessionStatus.completed
        if int(completed_version_count or 0) > 0
        else DecisionSessionStatus.created
    )


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
    evidence_by_repo = source_evidence["by_repo"].get(repo_full_name)
    if evidence_by_repo is not None:
        return evidence_by_repo

    evidence_by_slug = source_evidence["by_slug"].get(candidate_slug)
    if (
        evidence_by_slug is not None
        and _evidence_repo_full_name(evidence_by_slug) == repo_full_name
    ):
        return evidence_by_slug
    return None


def _evidence_repo_full_name(evidence: EvidenceItem) -> str:
    payload = dict(evidence.payload or {})
    return _clean_text(payload.get("full_name"))


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
