import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_api import models
from puppyrun_api.db import Base, get_session
from puppyrun_api.main import create_app
from puppyrun_api.repositories.sessions import create_decision_session


@pytest.fixture
async def phase3_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_returns_versioned_phase3_collections(phase3_client) -> None:
    assert hasattr(models, "ToolCall")
    assert hasattr(models, "Claim")
    assert hasattr(models, "RiskSignal")
    assert hasattr(models, "VerificationTask")

    client, maker = phase3_client
    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph and CrewAI for a stateful Agent runtime.",
        )
        version_one = models.DecisionVersion(
            session_id=session.id,
            version_number=1,
            label="Initial recommendation",
            status="completed",
            change_summary={"kind": "initial"},
            gap_analysis={"items": []},
            adr="Initial ADR.",
        )
        db.add(version_one)
        await db.flush()
        version_two = models.DecisionVersion(
            session_id=session.id,
            version_number=2,
            label="Risk verification revision",
            status="completed",
            source_version_id=version_one.id,
            change_summary={"kind": "phase3"},
            gap_analysis={"items": []},
            adr="Phase 3 ADR.",
        )
        db.add(version_two)
        await db.flush()

        v1_candidate = models.DecisionCandidate(
            session_id=session.id,
            decision_version_id=version_one.id,
            name="LangGraph",
            slug="langgraph",
            repo_full_name="langchain-ai/langgraph",
            include_reason="Baseline candidate.",
        )
        v2_candidate = models.DecisionCandidate(
            session_id=session.id,
            decision_version_id=version_two.id,
            name="CrewAI",
            slug="crewai",
            repo_full_name="crewAIInc/crewAI",
            include_reason="Revision candidate.",
        )
        criterion = models.DecisionCriterion(
            session_id=session.id,
            decision_version_id=version_two.id,
            name="Maintenance risk",
            weight=25,
            rationale="Maintenance matters.",
            evidence_needed="Issues and release evidence.",
        )
        db.add_all([v1_candidate, v2_candidate, criterion])
        await db.flush()

        evidence = models.EvidenceItem(
            session_id=session.id,
            decision_version_id=version_two.id,
            candidate_id=v2_candidate.id,
            criterion_id=criterion.id,
            source_type="github_issue",
            source_url="https://github.com/crewAIInc/crewAI/issues/1",
            title="CrewAI issue",
            summary="Issue mentions maintenance concern.",
            credibility="medium",
            payload={"content_hash": "hash-v2"},
        )
        db.add(evidence)
        await db.flush()

        v1_tool_call = models.ToolCall(
            session_id=session.id,
            decision_version_id=version_one.id,
            tool_name="direct_docs",
            status="completed",
            idempotency_key="v1-docs",
            source_type="official_docs",
            source_url="https://langchain-ai.github.io/langgraph/",
            request_summary="Fetch LangGraph docs.",
            response_summary="Fetched LangGraph docs.",
            payload={"result_count": 1},
        )
        v2_tool_call = models.ToolCall(
            session_id=session.id,
            decision_version_id=version_two.id,
            tool_name="github_issue_release",
            status="completed",
            idempotency_key="v2-github-issues",
            source_type="github_issue",
            source_url="https://github.com/crewAIInc/crewAI/issues/1",
            request_summary="Fetch CrewAI issue signals.",
            response_summary="Fetched CrewAI issue signals.",
            payload={"result_count": 1},
        )
        v1_claim = models.Claim(
            session_id=session.id,
            decision_version_id=version_one.id,
            candidate_id=v1_candidate.id,
            source_type="official_docs",
            source_url="https://langchain-ai.github.io/langgraph/",
            title="LangGraph docs claim",
            summary="LangGraph documents checkpointing.",
            citation_text="LangGraph checkpointing docs.",
            credibility="high",
            confidence=90,
            content_hash="claim-v1",
            payload={"source": "docs"},
        )
        v2_claim = models.Claim(
            session_id=session.id,
            decision_version_id=version_two.id,
            candidate_id=v2_candidate.id,
            criterion_id=criterion.id,
            source_evidence_item_id=evidence.id,
            source_type="github_issue",
            source_url="https://github.com/crewAIInc/crewAI/issues/1",
            title="CrewAI maintenance claim",
            summary="Community reports unresolved maintenance issues.",
            citation_text="Issue discussion summary.",
            credibility="medium",
            confidence=75,
            content_hash="claim-v2",
            payload={"source": "github_issue"},
        )
        db.add_all([v1_tool_call, v2_tool_call, v1_claim, v2_claim])
        await db.flush()

        v1_risk = models.RiskSignal(
            session_id=session.id,
            decision_version_id=version_one.id,
            candidate_id=v1_candidate.id,
            risk_key="maintenance",
            title="Maintenance risk",
            summary="Older baseline risk.",
            severity="low",
            status="contradicted",
            credibility="high",
            score_impact=0,
            supporting_claim_ids=[str(v1_claim.id)],
            verification_task_ids=[],
            payload={"version": 1},
        )
        v2_risk = models.RiskSignal(
            session_id=session.id,
            decision_version_id=version_two.id,
            candidate_id=v2_candidate.id,
            risk_key="maintenance",
            title="Maintenance risk",
            summary="Confirmed maintenance risk.",
            severity="medium",
            status="confirmed",
            credibility="medium",
            score_impact=-5,
            supporting_claim_ids=[str(v2_claim.id)],
            verification_task_ids=[],
            payload={"version": 2},
        )
        db.add_all([v1_risk, v2_risk])
        await db.flush()

        v1_task = models.VerificationTask(
            session_id=session.id,
            decision_version_id=version_one.id,
            candidate_id=v1_candidate.id,
            risk_signal_id=v1_risk.id,
            status="completed",
            verification_question="Check official docs for LangGraph maintenance.",
            stronger_source_type="official_docs",
            stronger_source_url="https://langchain-ai.github.io/langgraph/",
            verdict="contradicted",
            rationale="Official docs show supported maintenance path.",
            payload={"version": 1},
        )
        v2_task = models.VerificationTask(
            session_id=session.id,
            decision_version_id=version_two.id,
            candidate_id=v2_candidate.id,
            risk_signal_id=v2_risk.id,
            status="completed",
            verification_question="Check CrewAI issues and releases.",
            stronger_source_type="github_release",
            stronger_source_url="https://github.com/crewAIInc/crewAI/releases",
            verdict="confirmed",
            rationale="Release cadence did not contradict open issue risk.",
            payload={"version": 2},
        )
        db.add_all([v1_task, v2_task])
        await db.flush()
        v1_risk.verification_task_ids = [str(v1_task.id)]
        v2_risk.verification_task_ids = [str(v2_task.id)]
        await db.commit()
        session_id = session.id
        version_one_id = version_one.id

    response = await client.get(f"/api/v1/sessions/{session_id}/workspace")

    assert response.status_code == 200
    workspace = response.json()
    assert workspace["active_version"]["version_number"] == 2
    assert [tool_call["idempotency_key"] for tool_call in workspace["tool_calls"]] == [
        "v2-github-issues"
    ]
    assert [claim["content_hash"] for claim in workspace["claims"]] == ["claim-v2"]
    assert [risk["status"] for risk in workspace["risk_signals"]] == ["confirmed"]
    assert workspace["risk_signals"][0]["score_impact"] == -5
    assert [task["verdict"] for task in workspace["verification_tasks"]] == ["confirmed"]

    selected_response = await client.get(
        f"/api/v1/sessions/{session_id}/workspace",
        params={"version_id": str(version_one_id)},
    )

    assert selected_response.status_code == 200
    selected_workspace = selected_response.json()
    assert selected_workspace["active_version"]["version_number"] == 1
    assert [tool_call["idempotency_key"] for tool_call in selected_workspace["tool_calls"]] == [
        "v1-docs"
    ]
    assert [claim["content_hash"] for claim in selected_workspace["claims"]] == ["claim-v1"]
    assert [risk["status"] for risk in selected_workspace["risk_signals"]] == [
        "contradicted"
    ]
    assert [task["verdict"] for task in selected_workspace["verification_tasks"]] == [
        "contradicted"
    ]


@pytest.mark.asyncio
async def test_deleting_risk_signal_deletes_verification_tasks(phase3_client) -> None:
    _client, maker = phase3_client
    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph and CrewAI for a stateful Agent runtime.",
        )
        version = models.DecisionVersion(
            session_id=session.id,
            version_number=1,
            label="Risk verification",
            status="completed",
            change_summary={"kind": "phase3"},
            gap_analysis={"items": []},
        )
        db.add(version)
        await db.flush()
        candidate = models.DecisionCandidate(
            session_id=session.id,
            decision_version_id=version.id,
            name="CrewAI",
            slug="crewai",
            repo_full_name="crewAIInc/crewAI",
            include_reason="Candidate under review.",
        )
        db.add(candidate)
        await db.flush()
        risk = models.RiskSignal(
            session_id=session.id,
            decision_version_id=version.id,
            candidate_id=candidate.id,
            risk_key="maintenance",
            title="Maintenance risk",
            summary="Potential maintenance concern.",
            severity="medium",
            status="confirmed",
            credibility="medium",
            score_impact=-5,
        )
        db.add(risk)
        await db.flush()
        task = models.VerificationTask(
            session_id=session.id,
            decision_version_id=version.id,
            candidate_id=candidate.id,
            risk_signal_id=risk.id,
            status="completed",
            verification_question="Check release cadence.",
            verdict="confirmed",
        )
        db.add(task)
        await db.commit()

        await db.delete(risk)
        await db.commit()

        remaining_task = await db.get(models.VerificationTask, task.id)

    assert remaining_task is None
