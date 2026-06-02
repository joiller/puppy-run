import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_agent.workflow import run_phase1_workflow
from puppyrun_api.db import Base
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
        stale_candidate = DecisionCandidate(
            session_id=session.id,
            name="Stale Candidate",
            slug="stale",
            repo_full_name="stale/repo",
            include_reason="stale result from a previous run",
        )
        db.add(stale_candidate)
        await db.flush()
        db.add(
            DecisionCriterion(
                session_id=session.id,
                name="Stale criterion",
                weight=1,
                rationale="stale",
                evidence_needed="stale",
            )
        )
        db.add(
            EvidenceItem(
                session_id=session.id,
                candidate_id=stale_candidate.id,
                criterion_id=None,
                source_type="stale_source",
                source_url="https://example.com/stale",
                title="Stale evidence",
                summary="stale",
                credibility="low",
                payload={"stale": True},
            )
        )
        db.add(
            Recommendation(
                session_id=session.id,
                recommended_candidate_id=stale_candidate.id,
                summary="Recommended: stale.",
                rationale={"recommended_slug": "stale"},
            )
        )
        await db.commit()

    transport = httpx.MockTransport(github_handler)
    async with maker() as db:
        await run_phase1_workflow(db, run_id, github_transport=transport)

    async with maker() as db:
        refreshed = await db.get(DecisionSession, session_id)
        assert refreshed is not None
        assert refreshed.status == DecisionSessionStatus.completed
        assert refreshed.workflow_stage == "completed"
        assert "Recommended:" in (refreshed.current_summary or "")
        candidates = (
            (await db.execute(select(DecisionCandidate).order_by(DecisionCandidate.created_at)))
            .scalars()
            .all()
        )
        candidate_by_id = {candidate.id: candidate for candidate in candidates}
        candidate_by_slug = {candidate.slug: candidate for candidate in candidates}
        assert [candidate.slug for candidate in candidates] == [
            "langgraph",
            "openai_agents_sdk",
            "crewai",
        ]
        assert len((await db.execute(select(DecisionCriterion))).scalars().all()) == 5
        evidence_items = (await db.execute(select(EvidenceItem))).scalars().all()
        assert len(evidence_items) == 3
        for evidence in evidence_items:
            assert evidence.source_type == "github_repo"
            assert evidence.candidate_id is not None
            linked_candidate = candidate_by_id[evidence.candidate_id]
            assert evidence.payload["full_name"] == linked_candidate.repo_full_name
            assert evidence.source_url == f"https://github.com/{linked_candidate.repo_full_name}"
        recommendations = (await db.execute(select(Recommendation))).scalars().all()
        assert len(recommendations) == 1
        recommendation = recommendations[0]
        assert recommendation.recommended_candidate_id == candidate_by_slug["langgraph"].id
        assert recommendation.rationale["recommended_slug"] == "langgraph"
        assert recommendation.rationale["recommended_repo"] == "langchain-ai/langgraph"
        ranked = recommendation.rationale["ranked_candidates"]
        assert [candidate["slug"] for candidate in ranked] == [
            "langgraph",
            "openai_agents_sdk",
            "crewai",
        ]
        assert [candidate["score"] for candidate in ranked] == sorted(
            [candidate["score"] for candidate in ranked],
            reverse=True,
        )
        event_types = [
            event.event_type
            for event in (
                await db.execute(select(AgentEvent).order_by(AgentEvent.created_at))
            ).scalars()
        ]
        assert event_types == [
            "phase1_started",
            "criteria_generated",
            "github_repo_analyzed",
            "github_repo_analyzed",
            "github_repo_analyzed",
            "recommendation_generated",
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase1_workflow_marks_run_failed_when_github_analysis_fails() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph and OpenAI Agents SDK for a web Agent runtime.",
        )
        await append_user_message(
            db,
            session.id,
            "We need Python, checkpointing, human approval, and observability.",
        )
        run = await create_agent_run(db, session.id)
        run_id = run.id
        session_id = session.id

    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"message": "GitHub unavailable"})
    )
    async with maker() as db:
        with pytest.raises(httpx.HTTPStatusError):
            await run_phase1_workflow(db, run_id, github_transport=transport)

    async with maker() as db:
        refreshed = await db.get(DecisionSession, session_id)
        run = await db.get(AgentRun, run_id)
        assert refreshed is not None
        assert run is not None
        assert refreshed.status == DecisionSessionStatus.failed
        assert refreshed.workflow_stage == "failed"
        assert run.status == AgentRunStatus.failed
        events = (
            (
                await db.execute(
                    select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(
                        AgentEvent.created_at
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [event.event_type for event in events] == [
            "phase1_started",
            "phase1_failed",
        ]
        assert "GitHub unavailable" in events[-1].message

    await engine.dispose()
