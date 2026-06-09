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
    DecisionVersion,
    DecisionVersionStatus,
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
        repo_evidence = [
            evidence for evidence in evidence_items if evidence.source_type == "github_repo"
        ]
        phase3_evidence = [
            evidence for evidence in evidence_items if evidence.source_type == "github_issue"
        ]
        assert len(repo_evidence) == 3
        assert len(phase3_evidence) == 3
        for evidence in repo_evidence:
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
        assert event_types[:5] == [
            "phase1_started",
            "criteria_generated",
            "github_repo_analyzed",
            "github_repo_analyzed",
            "github_repo_analyzed",
        ]
        assert "phase3_sources_planned" in event_types
        assert "claims_extracted" in event_types
        assert "risks_clustered" in event_types
        assert "verification_tasks_created" in event_types
        assert "risk_verification_completed" in event_types
        assert "risk_adjusted_scores" in event_types
        assert event_types[-1] == "recommendation_generated"

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase1_completed_run_creates_version_one() -> None:
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
        versions = (await db.execute(select(DecisionVersion))).scalars().all()
        assert len(versions) == 1
        version = versions[0]
        assert version.session_id == session_id
        assert version.version_number == 1
        assert version.status == DecisionVersionStatus.completed
        assert version.source_version_id is None
        assert version.adr
        assert version.completed_at is not None

        candidates = (await db.execute(select(DecisionCandidate))).scalars().all()
        criteria = (await db.execute(select(DecisionCriterion))).scalars().all()
        evidence_items = (await db.execute(select(EvidenceItem))).scalars().all()
        recommendations = (await db.execute(select(Recommendation))).scalars().all()

        assert candidates
        assert criteria
        assert evidence_items
        assert len(recommendations) == 1
        assert {candidate.decision_version_id for candidate in candidates} == {version.id}
        assert {criterion.decision_version_id for criterion in criteria} == {version.id}
        assert {evidence.decision_version_id for evidence in evidence_items} == {version.id}
        assert recommendations[0].decision_version_id == version.id

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase1_rerun_preserves_completed_version_rows() -> None:
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
        first_run = await create_agent_run(db, session.id)
        first_run_id = first_run.id
        session_id = session.id

    transport = httpx.MockTransport(github_handler)
    async with maker() as db:
        await run_phase1_workflow(db, first_run_id, github_transport=transport)

    async with maker() as db:
        version_one = (await db.execute(select(DecisionVersion))).scalar_one()
        version_one_id = version_one.id
        version_one_candidate_ids = set(
            (
                await db.execute(
                    select(DecisionCandidate.id).where(
                        DecisionCandidate.decision_version_id == version_one_id
                    )
                )
            )
            .scalars()
            .all()
        )
        second_run = await create_agent_run(db, session_id)
        second_run_id = second_run.id

    async with maker() as db:
        await run_phase1_workflow(db, second_run_id, github_transport=transport)

    async with maker() as db:
        versions = (
            await db.execute(
                select(DecisionVersion).order_by(DecisionVersion.version_number)
            )
        ).scalars().all()
        assert [version.version_number for version in versions] == [1, 2]
        assert versions[0].status == DecisionVersionStatus.completed
        assert versions[1].status == DecisionVersionStatus.completed

        preserved_candidate_ids = set(
            (
                await db.execute(
                    select(DecisionCandidate.id).where(
                        DecisionCandidate.decision_version_id == version_one_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert preserved_candidate_ids == version_one_candidate_ids
        assert len(preserved_candidate_ids) == 3

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
