from uuid import UUID

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_agent.catalog import select_candidates
from puppyrun_agent.clarification import build_initial_context, update_context_with_answer
from puppyrun_agent.criteria import generate_criteria
from puppyrun_agent.github_client import GitHubClient
from puppyrun_agent.recommendation import build_recommendation, score_candidate
from puppyrun_api.config import get_settings
from puppyrun_api.models import (
    AgentEvent,
    AgentRun,
    AgentRunStatus,
    DecisionCandidate,
    DecisionCriterion,
    DecisionSession,
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
    await db.execute(delete(Recommendation).where(Recommendation.session_id == session.id))
    await db.execute(delete(EvidenceItem).where(EvidenceItem.session_id == session.id))
    await db.execute(delete(DecisionCriterion).where(DecisionCriterion.session_id == session.id))
    await db.execute(delete(DecisionCandidate).where(DecisionCandidate.session_id == session.id))

    context = _build_workflow_context(session)
    candidates = select_candidates(context)
    criteria = generate_criteria(context)

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
