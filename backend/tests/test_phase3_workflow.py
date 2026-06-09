import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_agent.workflow import run_phase1_workflow, run_phase2_workflow
from puppyrun_api.db import Base
from puppyrun_api.models import (
    AgentEvent,
    AgentRun,
    AgentRunStatus,
    Claim,
    DecisionSession,
    DecisionSessionStatus,
    DecisionVersion,
    DecisionVersionStatus,
    EvidenceItem,
    RiskSignal,
    ToolCall,
    VerificationTask,
)
from puppyrun_api.repositories.sessions import (
    create_agent_run,
    create_decision_session,
    create_phase2_version_run,
)
from puppyrun_api.repositories.workspace import append_user_message, get_workspace


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
            "open_issues_count": 125,
            "pushed_at": "2026-05-20T12:00:00Z",
            "license": {"spdx_id": "MIT"},
        },
    )


@pytest.mark.asyncio
async def test_phase1_workflow_persists_phase3_risk_rows_and_events() -> None:
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

    async with maker() as db:
        await run_phase1_workflow(
            db,
            run_id,
            github_transport=httpx.MockTransport(github_handler),
        )

    async with maker() as db:
        version = (await db.execute(select(DecisionVersion))).scalar_one()
        tool_calls = (await db.execute(select(ToolCall))).scalars().all()
        phase3_evidence = (
            (
                await db.execute(
                    select(EvidenceItem).where(EvidenceItem.source_type == "github_issue")
                )
            )
            .scalars()
            .all()
        )
        claims = (await db.execute(select(Claim))).scalars().all()
        risks = (await db.execute(select(RiskSignal))).scalars().all()
        tasks = (await db.execute(select(VerificationTask))).scalars().all()
        event_types = [
            event.event_type
            for event in (
                await db.execute(select(AgentEvent).order_by(AgentEvent.created_at))
            ).scalars()
        ]

        assert version.status == DecisionVersionStatus.completed
        assert version.gap_analysis["risk_adjusted_scores"]["langgraph"][
            "risk_adjustment"
        ] == -8
        assert {call.status for call in tool_calls} >= {"completed", "skipped"}
        assert {call.tool_name for call in tool_calls} >= {
            "phase3_candidate_sources",
            "phase3_tavily_search",
            "phase3_reddit_search",
        }
        assert phase3_evidence
        assert claims
        assert risks
        assert tasks
        assert {risk.status for risk in risks} == {"confirmed"}
        assert {risk.score_impact for risk in risks} == {-8}
        assert {task.status for task in tasks} == {"completed"}
        assert "phase3_sources_planned" in event_types
        assert "claims_extracted" in event_types
        assert "risks_clustered" in event_types
        assert "verification_tasks_created" in event_types
        assert "risk_verification_completed" in event_types
        assert "risk_adjusted_scores" in event_types

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase2_workflow_creates_versioned_phase3_rows_and_reuse_provenance() -> None:
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

    async with maker() as db:
        await run_phase1_workflow(
            db,
            first_run_id,
            github_transport=httpx.MockTransport(github_handler),
        )

    async with maker() as db:
        source_version = (await db.execute(select(DecisionVersion))).scalar_one()
        source_phase3_evidence_ids = set(
            (
                await db.execute(
                    select(EvidenceItem.id)
                    .where(EvidenceItem.decision_version_id == source_version.id)
                    .where(EvidenceItem.source_type == "github_issue")
                )
            )
            .scalars()
            .all()
        )

        parent_session = await db.get(DecisionSession, session_id)
        assert parent_session is not None
        context = dict(parent_session.decision_context or {})
        context["phase2_draft"] = {
            "source_version_id": str(source_version.id),
            "candidate_overrides": {},
            "custom_candidates": {},
            "must_include_constraints": {},
            "must_exclude_constraints": {},
            "weight_overrides": {
                "Observability and traceability": {
                    "weight": 45,
                    "reason": "Traceability is the main decision driver.",
                }
            },
        }
        parent_session.decision_context = context
        parent_session.status = DecisionSessionStatus.queued
        run, version = await create_phase2_version_run(db, session_id)
        run_id = run.id
        version_id = version.id

    async with maker() as db:
        await run_phase2_workflow(
            db,
            run_id,
            github_transport=httpx.MockTransport(lambda request: pytest.fail("unexpected fetch")),
        )

    async with maker() as db:
        version = await db.get(DecisionVersion, version_id)
        parent_session = await db.get(DecisionSession, session_id)
        assert version is not None
        assert parent_session is not None
        assert version.status == DecisionVersionStatus.completed
        assert version.gap_analysis["risk_adjusted_scores"]
        assert "phase3_risk_signals" not in version.gap_analysis
        assert "phase3_risk_signals" not in parent_session.decision_context[
            "phase2_gap_analysis"
        ]
        langgraph_scores = version.gap_analysis["risk_adjusted_scores"]["langgraph"]
        assert langgraph_scores["base_score"] > 0
        assert langgraph_scores["adjusted_score"] == max(
            0,
            langgraph_scores["base_score"] + langgraph_scores["risk_adjustment"],
        )
        assert langgraph_scores["adjusted_score"] > 0
        assert await get_workspace(db, session_id, version_id=source_version.id)

        phase3_evidence = (
            (
                await db.execute(
                    select(EvidenceItem)
                    .where(EvidenceItem.decision_version_id == version_id)
                    .where(EvidenceItem.source_type == "github_issue")
                )
            )
            .scalars()
            .all()
        )
        claims = (
            (
                await db.execute(select(Claim).where(Claim.decision_version_id == version_id))
            )
            .scalars()
            .all()
        )
        risks = (
            (
                await db.execute(
                    select(RiskSignal).where(RiskSignal.decision_version_id == version_id)
                )
            )
            .scalars()
            .all()
        )
        tasks = (
            (
                await db.execute(
                    select(VerificationTask).where(
                        VerificationTask.decision_version_id == version_id
                    )
                )
            )
            .scalars()
            .all()
        )

        assert phase3_evidence
        assert claims
        assert risks
        assert tasks
        assert {
            evidence.payload["reused_from_evidence_item_id"]
            for evidence in phase3_evidence
        } == {str(evidence_id) for evidence_id in source_phase3_evidence_ids}
        assert all(evidence.payload["repo_full_name"] for evidence in phase3_evidence)
        assert all(evidence.payload["source_profile_query"] for evidence in phase3_evidence)

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase2_phase3_provider_failure_marks_only_new_version_failed(
    monkeypatch,
) -> None:
    def failing_pipeline(_source_results):
        raise RuntimeError("provider failed with api_key=secret-api-key")

    monkeypatch.setattr(
        "puppyrun_agent.workflow.build_risk_verification_pipeline",
        failing_pipeline,
    )
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

    async with maker() as db:
        await run_phase1_workflow(
            db,
            first_run_id,
            github_transport=httpx.MockTransport(github_handler),
        )

    async with maker() as db:
        source_version = (await db.execute(select(DecisionVersion))).scalar_one()
        parent_session = await db.get(DecisionSession, session_id)
        assert parent_session is not None
        context = dict(parent_session.decision_context or {})
        context["phase2_draft"] = {
            "source_version_id": str(source_version.id),
            "candidate_overrides": {},
            "custom_candidates": {},
            "must_include_constraints": {},
            "must_exclude_constraints": {},
            "weight_overrides": {
                "Observability and traceability": {
                    "weight": 45,
                    "reason": "Traceability is the main decision driver.",
                }
            },
        }
        parent_session.decision_context = context
        parent_session.status = DecisionSessionStatus.queued
        run, version = await create_phase2_version_run(db, session_id)
        run_id = run.id
        version_id = version.id

    async with maker() as db:
        with pytest.raises(RuntimeError):
            await run_phase2_workflow(
                db,
                run_id,
                github_transport=httpx.MockTransport(
                    lambda request: pytest.fail("unexpected fetch")
                ),
            )

    async with maker() as db:
        source_version = await db.get(DecisionVersion, source_version.id)
        failed_version = await db.get(DecisionVersion, version_id)
        run = await db.get(AgentRun, run_id)
        assert source_version is not None
        assert failed_version is not None
        assert run is not None
        assert source_version.status == DecisionVersionStatus.completed
        assert failed_version.status == DecisionVersionStatus.failed
        assert "secret-api-key" not in failed_version.gap_analysis["failure"]["error"]
        assert "[redacted]" in failed_version.gap_analysis["failure"]["error"]
        assert run.status == AgentRunStatus.failed

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase3_source_failure_records_sanitized_tool_error(monkeypatch) -> None:
    async def failing_source_handler(_context, _inputs):
        raise RuntimeError("upstream failed with api_key=secret-api-key")

    monkeypatch.setattr(
        "puppyrun_agent.workflow._phase3_candidate_source_handler",
        failing_source_handler,
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph and OpenAI Agents SDK for a web Agent runtime.",
        )
        await append_user_message(db, session.id, "We need Python and checkpointing.")
        run = await create_agent_run(db, session.id)
        run_id = run.id

    async with maker() as db:
        await run_phase1_workflow(
            db,
            run_id,
            github_transport=httpx.MockTransport(github_handler),
        )

    async with maker() as db:
        run = await db.get(AgentRun, run_id)
        failed_calls = (
            (
                await db.execute(
                    select(ToolCall)
                    .where(ToolCall.tool_name == "phase3_candidate_sources")
                    .where(ToolCall.status == "failed")
                )
            )
            .scalars()
            .all()
        )
        assert run is not None
        assert run.status == AgentRunStatus.completed
        assert failed_calls
        assert all("secret-api-key" not in (call.error or "") for call in failed_calls)
        assert all("[redacted]" in (call.error or "") for call in failed_calls)

    await engine.dispose()
