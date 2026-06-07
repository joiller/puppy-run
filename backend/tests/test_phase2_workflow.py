import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from puppyrun_agent.criteria import generate_criteria
from puppyrun_agent.phase2 import normalize_phase2_draft
from puppyrun_agent.workflow import run_phase2_workflow
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
    ScoreCell,
)
from puppyrun_api.repositories.sessions import create_decision_session, create_phase2_version_run
from puppyrun_api.repositories.workspace import get_workspace

BASELINE_CONTEXT = {
    "constraints": ["python", "checkpointing", "observability"],
    "mentioned_candidates": ["langgraph", "openai_agents_sdk"],
    "clarification": {
        "status": "answered",
        "answer": "Python, checkpointing, human approval, and observability matter.",
    },
}


@pytest.mark.asyncio
async def test_phase2_weight_only_rerun_reuses_evidence_without_github_fetches() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session_id, source_version_id = await _seed_completed_baseline(db)
        await _store_phase2_draft(
            db,
            session_id,
            {
                "source_version_id": str(source_version_id),
                "candidate_overrides": {},
                "custom_candidates": {},
                "must_include_constraints": {},
                "must_exclude_constraints": {},
                "weight_overrides": {
                    "Observability and traceability": {
                        "weight": 45,
                        "reason": "Traceability is now the main decision driver.",
                    }
                },
            },
        )
        run_id, version_id = await _queue_phase2_version(db, session_id, source_version_id)

    fetched_repos: list[str] = []

    def unexpected_fetch(request: httpx.Request) -> httpx.Response:
        fetched_repos.append(request.url.path.removeprefix("/repos/"))
        pytest.fail(f"weight-only rerun fetched {request.url.path}")

    async with maker() as db:
        summary = await run_phase2_workflow(
            db,
            run_id,
            github_transport=httpx.MockTransport(unexpected_fetch),
        )

    assert fetched_repos == []
    assert summary.startswith("Recommended v2:")

    async with maker() as db:
        session = await db.get(DecisionSession, session_id)
        version = await db.get(DecisionVersion, version_id)
        run = await db.get(AgentRun, run_id)
        assert session is not None
        assert version is not None
        assert run is not None
        assert session.status == DecisionSessionStatus.completed
        assert session.workflow_stage == "completed"
        assert "phase2_draft" not in session.decision_context
        assert version.status == DecisionVersionStatus.completed
        assert version.completed_at is not None
        assert version.gap_analysis["score_only"] is True
        assert version.gap_analysis["requires_github_fetch"] is False
        assert version.adr is not None
        assert "## Decision" in version.adr
        assert run.status == AgentRunStatus.completed

        candidates = await _version_rows(db, DecisionCandidate, version_id)
        criteria = await _version_rows(db, DecisionCriterion, version_id)
        evidence_items = await _version_rows(db, EvidenceItem, version_id)
        score_cells = await _version_rows(db, ScoreCell, version_id)
        recommendations = await _version_rows(db, Recommendation, version_id)

        assert [candidate.slug for candidate in candidates] == [
            "langgraph",
            "openai_agents_sdk",
        ]
        assert len(criteria) == 5
        assert {evidence.payload["full_name"] for evidence in evidence_items} == {
            "langchain-ai/langgraph",
            "openai/openai-agents-python",
        }
        assert len(score_cells) == len(candidates) * len(criteria)
        assert len(recommendations) == 1
        assert recommendations[0].decision_version_id == version_id

        event_types = await _event_types(db, run_id)
        assert event_types[:2] == ["phase2_started", "targeted_research_planned"]
        assert "recommendation_version_created" in event_types
        assert "recommendation_generated" not in event_types

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase2_uses_queued_draft_snapshot_when_session_draft_changes() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session_id, source_version_id = await _seed_completed_baseline(db)
        await _store_phase2_draft(
            db,
            session_id,
            {
                "source_version_id": str(source_version_id),
                "candidate_overrides": {},
                "custom_candidates": {},
                "must_include_constraints": {},
                "must_exclude_constraints": {},
                "weight_overrides": {
                    "Observability and traceability": {
                        "weight": 45,
                        "reason": "Traceability is the original queued driver.",
                    }
                },
            },
        )
        run, version = await create_phase2_version_run(db, session_id)
        run_id = run.id
        version_id = version.id

        session = await db.get(DecisionSession, session_id)
        assert session is not None
        context = dict(session.decision_context or {})
        context["phase2_draft"] = {
            "source_version_id": str(source_version_id),
            "candidate_overrides": {},
            "custom_candidates": {
                "autogen": {
                    "name": "AutoGen",
                    "slug": "autogen",
                    "repo_full_name": "microsoft/autogen",
                    "reason": "This later edit must not affect the queued run.",
                }
            },
            "must_include_constraints": {},
            "must_exclude_constraints": {},
            "weight_overrides": {},
        }
        session.decision_context = context
        session.workflow_stage = "context_changed"
        await db.commit()

    def unexpected_fetch(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"queued snapshot workflow fetched {request.url.path}")

    async with maker() as db:
        await run_phase2_workflow(
            db,
            run_id,
            github_transport=httpx.MockTransport(unexpected_fetch),
        )

    async with maker() as db:
        session = await db.get(DecisionSession, session_id)
        version = await db.get(DecisionVersion, version_id)
        assert session is not None
        assert version is not None
        assert session.status == DecisionSessionStatus.completed
        assert session.workflow_stage == "context_changed"
        latest_draft = session.decision_context["phase2_draft"]
        assert latest_draft["custom_candidates"] == {
            "autogen": {
                "name": "AutoGen",
                "slug": "autogen",
                "repo_full_name": "microsoft/autogen",
                "reason": "This later edit must not affect the queued run.",
            }
        }
        assert version.status == DecisionVersionStatus.completed
        assert version.source_version_id == source_version_id
        assert version.change_summary["phase2_draft"]["source_version_id"] == str(
            source_version_id
        )
        assert version.gap_analysis["score_only"] is True
        assert version.gap_analysis["requires_github_fetch"] is False

        candidates = await _version_rows(db, DecisionCandidate, version_id)
        assert [candidate.slug for candidate in candidates] == [
            "langgraph",
            "openai_agents_sdk",
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase2_stale_failure_preserves_newer_session_draft_state() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session_id, source_version_id = await _seed_completed_baseline(db)
        await _store_phase2_draft(
            db,
            session_id,
            {
                "source_version_id": str(source_version_id),
                "candidate_overrides": {},
                "custom_candidates": {
                    "autogen": {
                        "name": "AutoGen",
                        "slug": "autogen",
                        "repo_full_name": "microsoft/autogen",
                        "reason": "Team asked to compare AutoGen.",
                    }
                },
                "must_include_constraints": {},
                "must_exclude_constraints": {},
                "weight_overrides": {},
            },
        )
        run_id, version_id = await _queue_phase2_version(db, session_id, source_version_id)

        session = await db.get(DecisionSession, session_id)
        assert session is not None
        context = dict(session.decision_context or {})
        context["phase2_draft"] = {
            "source_version_id": str(source_version_id),
            "candidate_overrides": {},
            "custom_candidates": {},
            "must_include_constraints": {},
            "must_exclude_constraints": {},
            "weight_overrides": {
                "Observability and traceability": {
                    "weight": 45,
                    "reason": "This later edit must stay active after the stale failure.",
                }
            },
        }
        session.decision_context = context
        session.workflow_stage = "context_changed"
        await db.commit()

    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"message": "GitHub unavailable"})
    )
    async with maker() as db:
        with pytest.raises(httpx.HTTPStatusError):
            await run_phase2_workflow(db, run_id, github_transport=transport)

    async with maker() as db:
        session = await db.get(DecisionSession, session_id)
        source_version = await db.get(DecisionVersion, source_version_id)
        failed_version = await db.get(DecisionVersion, version_id)
        run = await db.get(AgentRun, run_id)
        assert session is not None
        assert source_version is not None
        assert failed_version is not None
        assert run is not None

        assert session.status == DecisionSessionStatus.completed
        assert session.workflow_stage == "context_changed"
        latest_draft = session.decision_context["phase2_draft"]
        assert latest_draft["weight_overrides"] == {
            "Observability and traceability": {
                "weight": 45,
                "reason": "This later edit must stay active after the stale failure.",
            }
        }
        assert source_version.status == DecisionVersionStatus.completed
        assert failed_version.status == DecisionVersionStatus.failed
        assert failed_version.gap_analysis["failure"]["error"] == "GitHub unavailable"
        assert run.status == AgentRunStatus.failed
        assert await _event_types(db, run_id) == [
            "phase2_started",
            "targeted_research_planned",
            "phase2_failed",
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase2_added_candidate_fetches_only_new_github_repo() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session_id, source_version_id = await _seed_completed_baseline(db)
        await _store_phase2_draft(
            db,
            session_id,
            {
                "source_version_id": str(source_version_id),
                "candidate_overrides": {},
                "custom_candidates": {
                    "autogen": {
                        "name": "AutoGen",
                        "slug": "autogen",
                        "repo_full_name": "microsoft/autogen",
                        "reason": "Team asked to compare AutoGen.",
                    }
                },
                "must_include_constraints": {},
                "must_exclude_constraints": {},
                "weight_overrides": {},
            },
        )
        run_id, version_id = await _queue_phase2_version(db, session_id, source_version_id)

    fetched_repos: list[str] = []

    def fetch_only_autogen(request: httpx.Request) -> httpx.Response:
        repo_name = request.url.path.removeprefix("/repos/")
        fetched_repos.append(repo_name)
        if repo_name != "microsoft/autogen":
            pytest.fail(f"unexpected GitHub fetch for {repo_name}")
        return _github_response(repo_name, stars=12000)

    async with maker() as db:
        await run_phase2_workflow(
            db,
            run_id,
            github_transport=httpx.MockTransport(fetch_only_autogen),
        )

    assert fetched_repos == ["microsoft/autogen"]

    async with maker() as db:
        version = await db.get(DecisionVersion, version_id)
        assert version is not None
        assert version.status == DecisionVersionStatus.completed
        assert version.gap_analysis["requires_github_fetch"] is True
        assert version.gap_analysis["research_tasks"] == [
            {
                "candidate_slug": "autogen",
                "repo_full_name": "microsoft/autogen",
                "reason": "missing_github_evidence",
            }
        ]

        candidates = await _version_rows(db, DecisionCandidate, version_id)
        evidence_items = await _version_rows(db, EvidenceItem, version_id)
        score_cells = await _version_rows(db, ScoreCell, version_id)
        recommendations = await _version_rows(db, Recommendation, version_id)

        assert [candidate.slug for candidate in candidates] == [
            "langgraph",
            "openai_agents_sdk",
            "autogen",
        ]
        assert {evidence.payload["full_name"] for evidence in evidence_items} == {
            "langchain-ai/langgraph",
            "openai/openai-agents-python",
            "microsoft/autogen",
        }
        assert len(score_cells) == len(candidates) * 5
        assert recommendations[0].rationale["recommended_version"] == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase2_same_slug_different_repo_fetches_and_persists_new_github_evidence() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session_id, source_version_id = await _seed_completed_baseline(db)
        await _store_phase2_draft(
            db,
            session_id,
            {
                "source_version_id": str(source_version_id),
                "candidate_overrides": {},
                "custom_candidates": {
                    "langgraph": {
                        "name": "LangGraph Fork",
                        "slug": "langgraph",
                        "repo_full_name": "example/langgraph-fork",
                        "reason": "Compare a maintained internal fork under the same slug.",
                    }
                },
                "must_include_constraints": {},
                "must_exclude_constraints": {},
                "weight_overrides": {},
            },
        )
        run_id, version_id = await _queue_phase2_version(db, session_id, source_version_id)

    fetched_repos: list[str] = []

    def fetch_only_changed_repo(request: httpx.Request) -> httpx.Response:
        repo_name = request.url.path.removeprefix("/repos/")
        fetched_repos.append(repo_name)
        if repo_name != "example/langgraph-fork":
            pytest.fail(f"unexpected GitHub fetch for {repo_name}")
        return _github_response(repo_name, stars=18000)

    async with maker() as db:
        await run_phase2_workflow(
            db,
            run_id,
            github_transport=httpx.MockTransport(fetch_only_changed_repo),
        )

    assert fetched_repos == ["example/langgraph-fork"]

    async with maker() as db:
        version = await db.get(DecisionVersion, version_id)
        assert version is not None
        assert version.status == DecisionVersionStatus.completed
        assert version.gap_analysis["research_tasks"] == [
            {
                "candidate_slug": "langgraph",
                "repo_full_name": "example/langgraph-fork",
                "reason": "missing_github_evidence",
            }
        ]

        candidates = await _version_rows(db, DecisionCandidate, version_id)
        evidence_items = await _version_rows(db, EvidenceItem, version_id)
        assert {
            (candidate.slug, candidate.repo_full_name) for candidate in candidates
        } == {
            ("langgraph", "example/langgraph-fork"),
            ("openai_agents_sdk", "openai/openai-agents-python"),
        }
        assert {evidence.payload["full_name"] for evidence in evidence_items} == {
            "example/langgraph-fork",
            "openai/openai-agents-python",
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase2_invalid_queued_snapshot_marks_run_and_version_failed() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session_id, source_version_id = await _seed_completed_baseline(db)
        run = AgentRun(session_id=session_id, status=AgentRunStatus.queued)
        db.add(run)
        await db.flush()
        version = DecisionVersion(
            session_id=session_id,
            version_number=2,
            label="Phase 2 revision",
            status=DecisionVersionStatus.queued,
            source_version_id=source_version_id,
            change_summary={
                "agent_run_id": str(run.id),
                "kind": "phase2_targeted",
                "phase2_draft": {
                    "source_version_id": "00000000-0000-0000-0000-000000000001",
                    "candidate_overrides": {},
                    "custom_candidates": {},
                    "must_include_constraints": {},
                    "must_exclude_constraints": {},
                    "weight_overrides": {
                        "Observability and traceability": {
                            "weight": 45,
                            "reason": "Traceability remains important.",
                        }
                    },
                },
            },
            gap_analysis={},
        )
        db.add(version)
        session = await db.get(DecisionSession, session_id)
        assert session is not None
        session.status = DecisionSessionStatus.queued
        session.workflow_stage = "queued"
        await db.commit()
        run_id = run.id
        version_id = version.id

    def unexpected_fetch(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"invalid snapshot workflow fetched {request.url.path}")

    async with maker() as db:
        with pytest.raises(ValueError, match="no completed source version found"):
            await run_phase2_workflow(
                db,
                run_id,
                github_transport=httpx.MockTransport(unexpected_fetch),
            )

    async with maker() as db:
        source_version = await db.get(DecisionVersion, source_version_id)
        failed_version = await db.get(DecisionVersion, version_id)
        run = await db.get(AgentRun, run_id)
        session = await db.get(DecisionSession, session_id)
        assert source_version is not None
        assert failed_version is not None
        assert run is not None
        assert session is not None

        assert source_version.status == DecisionVersionStatus.completed
        assert failed_version.status == DecisionVersionStatus.failed
        assert failed_version.gap_analysis["failure"] == {
            "error": "no completed source version found",
            "error_type": "ValueError",
        }
        assert run.status == AgentRunStatus.failed
        assert session.status == DecisionSessionStatus.failed
        assert session.workflow_stage == "failed"
        assert await _event_types(db, run_id) == ["phase2_failed"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase2_failure_marks_only_new_version_failed() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session_id, source_version_id = await _seed_completed_baseline(db)
        await _store_phase2_draft(
            db,
            session_id,
            {
                "source_version_id": str(source_version_id),
                "candidate_overrides": {},
                "custom_candidates": {
                    "autogen": {
                        "name": "AutoGen",
                        "slug": "autogen",
                        "repo_full_name": "microsoft/autogen",
                        "reason": "Team asked to compare AutoGen.",
                    }
                },
                "must_include_constraints": {},
                "must_exclude_constraints": {},
                "weight_overrides": {},
            },
        )
        run_id, version_id = await _queue_phase2_version(db, session_id, source_version_id)

    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"message": "GitHub unavailable"})
    )
    async with maker() as db:
        with pytest.raises(httpx.HTTPStatusError):
            await run_phase2_workflow(db, run_id, github_transport=transport)

    async with maker() as db:
        session = await db.get(DecisionSession, session_id)
        source_version = await db.get(DecisionVersion, source_version_id)
        failed_version = await db.get(DecisionVersion, version_id)
        run = await db.get(AgentRun, run_id)
        assert session is not None
        assert source_version is not None
        assert failed_version is not None
        assert run is not None

        assert session.status == DecisionSessionStatus.failed
        assert session.workflow_stage == "failed"
        assert source_version.status == DecisionVersionStatus.completed
        assert failed_version.status == DecisionVersionStatus.failed
        assert failed_version.gap_analysis["failure"]["error"] == "GitHub unavailable"
        assert run.status == AgentRunStatus.failed

        source_workspace = await get_workspace(db, session_id, version_id=source_version_id)
        assert source_workspace.active_version is not None
        assert source_workspace.active_version.id == source_version_id
        assert [candidate.slug for candidate in source_workspace.candidates] == [
            "langgraph",
            "openai_agents_sdk",
        ]
        assert await _event_types(db, run_id) == [
            "phase2_started",
            "targeted_research_planned",
            "phase2_failed",
        ]

    await engine.dispose()


async def _seed_completed_baseline(db: AsyncSession) -> tuple:
    session = await create_decision_session(
        db,
        "Compare LangGraph and OpenAI Agents SDK for a stateful Python Agent runtime.",
    )
    session.workflow_stage = "completed"
    session.status = DecisionSessionStatus.completed
    session.current_summary = "Recommended: LangGraph."
    session.decision_context = dict(BASELINE_CONTEXT)
    version = DecisionVersion(
        session_id=session.id,
        version_number=1,
        label="Phase 1 baseline",
        status=DecisionVersionStatus.completed,
        change_summary={"kind": "phase1_baseline"},
        gap_analysis={"items": []},
        adr="ADR v1: Recommended LangGraph.",
    )
    db.add(version)
    await db.flush()

    langgraph = DecisionCandidate(
        session_id=session.id,
        decision_version_id=version.id,
        name="LangGraph",
        slug="langgraph",
        repo_full_name="langchain-ai/langgraph",
        include_reason="Baseline candidate.",
        health_summary="LangGraph repository health.",
        health_metrics=_repo_payload("langchain-ai/langgraph", stars=50000),
        score=95,
    )
    openai_agents = DecisionCandidate(
        session_id=session.id,
        decision_version_id=version.id,
        name="OpenAI Agents SDK",
        slug="openai_agents_sdk",
        repo_full_name="openai/openai-agents-python",
        include_reason="Baseline candidate.",
        health_summary="OpenAI Agents SDK repository health.",
        health_metrics=_repo_payload("openai/openai-agents-python", stars=25000),
        score=90,
    )
    db.add_all([langgraph, openai_agents])
    await db.flush()

    criteria = [
        DecisionCriterion(
            session_id=session.id,
            decision_version_id=version.id,
            name=criterion.name,
            weight=criterion.weight,
            rationale=criterion.rationale,
            evidence_needed=criterion.evidence_needed,
        )
        for criterion in generate_criteria(BASELINE_CONTEXT)
    ]
    db.add_all(criteria)
    await db.flush()

    db.add_all(
        [
            EvidenceItem(
                session_id=session.id,
                decision_version_id=version.id,
                candidate_id=langgraph.id,
                criterion_id=None,
                source_type="github_repo",
                source_url="https://github.com/langchain-ai/langgraph",
                title="GitHub repository health for LangGraph",
                summary="LangGraph has strong repository health.",
                credibility="medium",
                payload=_repo_payload("langchain-ai/langgraph", stars=50000),
            ),
            EvidenceItem(
                session_id=session.id,
                decision_version_id=version.id,
                candidate_id=openai_agents.id,
                criterion_id=None,
                source_type="github_repo",
                source_url="https://github.com/openai/openai-agents-python",
                title="GitHub repository health for OpenAI Agents SDK",
                summary="OpenAI Agents SDK has strong repository health.",
                credibility="medium",
                payload=_repo_payload("openai/openai-agents-python", stars=25000),
            ),
            Recommendation(
                session_id=session.id,
                decision_version_id=version.id,
                recommended_candidate_id=langgraph.id,
                summary="Recommended: LangGraph.",
                rationale={
                    "recommended_slug": "langgraph",
                    "ranked_candidates": [
                        {"slug": "langgraph", "score": 95},
                        {"slug": "openai_agents_sdk", "score": 90},
                    ],
                },
            ),
        ]
    )
    await db.commit()
    return session.id, version.id


async def _store_phase2_draft(
    db: AsyncSession,
    session_id,
    draft: dict,
) -> None:
    session = await db.get(DecisionSession, session_id)
    assert session is not None
    context = dict(session.decision_context or {})
    context["phase2_draft"] = draft
    session.decision_context = context
    session.workflow_stage = "context_changed"
    await db.commit()


async def _queue_phase2_version(
    db: AsyncSession,
    session_id,
    source_version_id,
) -> tuple:
    run = AgentRun(session_id=session_id, status=AgentRunStatus.queued)
    db.add(run)
    await db.flush()
    session = await db.get(DecisionSession, session_id)
    assert session is not None
    raw_draft = (session.decision_context or {}).get("phase2_draft")
    draft = normalize_phase2_draft(raw_draft, source_version_id)
    version = DecisionVersion(
        session_id=session_id,
        version_number=2,
        label="Phase 2 revision",
        status=DecisionVersionStatus.queued,
        source_version_id=source_version_id,
        change_summary={
            "agent_run_id": str(run.id),
            "kind": "phase2_targeted",
            "source_version_id": str(source_version_id),
            "phase2_draft": draft,
        },
        gap_analysis={},
    )
    db.add(version)
    session.status = DecisionSessionStatus.queued
    await db.commit()
    return run.id, version.id


async def _version_rows(db: AsyncSession, model, version_id) -> list:
    return list(
        (
            await db.execute(
                select(model)
                .where(model.decision_version_id == version_id)
                .order_by(model.created_at.asc(), model.id.asc())
            )
        ).scalars()
    )


async def _event_types(db: AsyncSession, run_id) -> list[str]:
    return [
        event.event_type
        for event in (
            await db.execute(
                select(AgentEvent)
                .where(AgentEvent.run_id == run_id)
                .order_by(AgentEvent.created_at.asc(), AgentEvent.id.asc())
            )
        ).scalars()
    ]


def _github_response(repo_name: str, *, stars: int = 10000) -> httpx.Response:
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


def _repo_payload(repo_name: str, *, stars: int) -> dict:
    return {
        "full_name": repo_name,
        "stars": stars,
        "forks": 1000,
        "open_issues": 100,
        "pushed_at": "2026-05-20T12:00:00Z",
        "license_spdx_id": "MIT",
    }
