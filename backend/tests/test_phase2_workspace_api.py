import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_api import models
from puppyrun_api.db import Base, get_session
from puppyrun_api.main import create_app
from puppyrun_api.repositories.sessions import create_decision_session


@pytest.fixture
async def phase2_client():
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
async def test_workspace_returns_versions_active_version_and_score_cells(phase2_client) -> None:
    assert hasattr(models, "DecisionVersion")
    assert hasattr(models, "ScoreCell")
    DecisionVersion = models.DecisionVersion
    ScoreCell = models.ScoreCell

    client, maker = phase2_client
    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime.",
        )
        version_one = DecisionVersion(
            session_id=session.id,
            version_number=1,
            label="Initial recommendation",
            status="completed",
            change_summary={"kind": "initial"},
            gap_analysis={"items": [{"message": "baseline"}]},
            adr="ADR for the initial recommendation.",
        )
        db.add(version_one)
        await db.flush()
        version_two = DecisionVersion(
            session_id=session.id,
            version_number=2,
            label="Human approval revision",
            status="running",
            source_version_id=version_one.id,
            change_summary={"changed_weights": ["Runtime control and state"]},
            gap_analysis={"requires_research": False, "score_only": True, "items": []},
        )
        db.add(version_two)
        await db.flush()

        session.decision_context["phase2_draft"] = {
            "source_version_id": str(version_one.id),
            "candidate_overrides": {
                "openai_agents_sdk": {
                    "action": "lock",
                    "reason": "The team wants to keep it in comparison.",
                }
            },
            "custom_candidates": {},
            "must_include_constraints": {},
            "must_exclude_constraints": {},
            "weight_overrides": {},
        }
        session.decision_context["phase2_gap_analysis"] = {
            "requires_research": False,
            "score_only": True,
            "changed_candidates": [],
            "changed_constraints": [],
            "changed_weights": ["Runtime control and state"],
            "research_tasks": [],
            "reuse_tasks": [{"candidate_slug": "openai_agents_sdk"}],
            "items": [{"kind": "weight_change", "label": "Runtime control and state"}],
        }

        v1_candidate = models.DecisionCandidate(
            session_id=session.id,
            decision_version_id=version_one.id,
            name="LangGraph",
            slug="langgraph",
            repo_full_name="langchain-ai/langgraph",
            include_reason="Baseline candidate.",
            score=94,
        )
        v2_candidate = models.DecisionCandidate(
            session_id=session.id,
            decision_version_id=version_two.id,
            name="OpenAI Agents SDK",
            slug="openai_agents_sdk",
            repo_full_name="openai/openai-agents-python",
            include_reason="Explicitly locked for revision.",
            selection_state="locked",
            is_locked=True,
            score=88,
        )
        v1_criterion = models.DecisionCriterion(
            session_id=session.id,
            decision_version_id=version_one.id,
            name="Runtime control and state",
            weight=30,
            rationale="State matters.",
            evidence_needed="Repository activity and runtime features.",
        )
        v2_criterion = models.DecisionCriterion(
            session_id=session.id,
            decision_version_id=version_two.id,
            name="Runtime control and state",
            weight=45,
            rationale="Human approval and recovery matter more.",
            evidence_needed="Repository activity and runtime features.",
            is_locked=True,
        )
        db.add_all([v1_candidate, v2_candidate, v1_criterion, v2_criterion])
        await db.flush()

        v1_evidence = models.EvidenceItem(
            session_id=session.id,
            decision_version_id=version_one.id,
            candidate_id=v1_candidate.id,
            criterion_id=v1_criterion.id,
            source_type="github_repo",
            source_url="https://github.com/langchain-ai/langgraph",
            title="LangGraph repository",
            summary="Strong activity.",
            credibility="high",
            payload={"stars": 50000},
        )
        v2_evidence = models.EvidenceItem(
            session_id=session.id,
            decision_version_id=version_two.id,
            candidate_id=v2_candidate.id,
            criterion_id=v2_criterion.id,
            source_type="github_repo",
            source_url="https://github.com/openai/openai-agents-python",
            title="OpenAI Agents SDK repository",
            summary="Active SDK repository.",
            credibility="high",
            payload={"stars": 25000},
        )
        db.add_all([v1_evidence, v2_evidence])
        await db.flush()

        db.add_all(
            [
                models.Recommendation(
                    session_id=session.id,
                    decision_version_id=version_one.id,
                    recommended_candidate_id=v1_candidate.id,
                    summary="Recommended: LangGraph.",
                    rationale={"recommended_slug": "langgraph"},
                ),
                models.Recommendation(
                    session_id=session.id,
                    decision_version_id=version_two.id,
                    recommended_candidate_id=v2_candidate.id,
                    summary="Recommended v2: OpenAI Agents SDK.",
                    rationale={"recommended_slug": "openai_agents_sdk"},
                ),
                ScoreCell(
                    session_id=session.id,
                    decision_version_id=version_two.id,
                    candidate_id=v2_candidate.id,
                    criterion_id=v2_criterion.id,
                    score=82,
                    status="supported",
                    explanation="Repository evidence supports this criterion.",
                    evidence_item_ids=[str(v2_evidence.id)],
                ),
            ]
        )
        await db.commit()
        session_id = session.id
        version_one_id = version_one.id

    response = await client.get(f"/api/v1/sessions/{session_id}/workspace")

    assert response.status_code == 200
    workspace = response.json()
    assert [version["version_number"] for version in workspace["versions"]] == [1, 2]
    assert workspace["active_version"]["version_number"] == 2
    assert workspace["draft"]["source_version_id"] == str(version_one_id)
    assert workspace["gap_analysis"]["score_only"] is True
    assert [candidate["slug"] for candidate in workspace["candidates"]] == [
        "openai_agents_sdk"
    ]
    assert workspace["candidates"][0]["decision_version_id"] == workspace["active_version"]["id"]
    assert workspace["candidates"][0]["selection_state"] == "locked"
    assert workspace["candidates"][0]["is_locked"] is True
    assert workspace["criteria"][0]["is_locked"] is True
    assert [recommendation["summary"] for recommendation in workspace["recommendations"]] == [
        "Recommended v2: OpenAI Agents SDK."
    ]
    assert len(workspace["score_cells"]) == 1
    assert workspace["score_cells"][0]["status"] == "supported"
    assert workspace["score_cells"][0]["evidence_item_ids"] == [
        workspace["evidence_items"][0]["id"]
    ]

    selected_response = await client.get(
        f"/api/v1/sessions/{session_id}/workspace",
        params={"version_id": str(version_one_id)},
    )

    assert selected_response.status_code == 200
    selected_workspace = selected_response.json()
    assert selected_workspace["active_version"]["version_number"] == 1
    assert [candidate["slug"] for candidate in selected_workspace["candidates"]] == [
        "langgraph"
    ]
    selected_summaries = [
        recommendation["summary"] for recommendation in selected_workspace["recommendations"]
    ]
    assert selected_summaries == ["Recommended: LangGraph."]
    assert selected_workspace["score_cells"] == []


@pytest.mark.asyncio
async def test_workspace_without_versions_remains_readable(phase2_client) -> None:
    client, maker = phase2_client
    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph and CrewAI for a web Agent runtime.",
        )
        candidate = models.DecisionCandidate(
            session_id=session.id,
            name="LangGraph",
            slug="langgraph",
            repo_full_name="langchain-ai/langgraph",
            include_reason="Legacy candidate row.",
            score=90,
        )
        criterion = models.DecisionCriterion(
            session_id=session.id,
            name="Runtime control and state",
            weight=40,
            rationale="State matters.",
            evidence_needed="Repository evidence.",
        )
        db.add_all([candidate, criterion])
        await db.flush()
        evidence = models.EvidenceItem(
            session_id=session.id,
            candidate_id=candidate.id,
            criterion_id=criterion.id,
            source_type="github_repo",
            source_url="https://github.com/langchain-ai/langgraph",
            title="LangGraph repository",
            summary="Legacy evidence row.",
            credibility="high",
            payload={},
        )
        db.add(evidence)
        await db.flush()
        db.add(
            models.Recommendation(
                session_id=session.id,
                recommended_candidate_id=candidate.id,
                summary="Recommended: LangGraph.",
                rationale={"recommended_slug": "langgraph"},
            )
        )
        await db.commit()
        session_id = session.id

    response = await client.get(f"/api/v1/sessions/{session_id}/workspace")

    assert response.status_code == 200
    workspace = response.json()
    assert workspace["versions"] == []
    assert workspace["active_version"] is None
    assert workspace["draft"]["candidate_overrides"] == {}
    assert workspace["gap_analysis"]["items"] == []
    assert workspace["score_cells"] == []
    assert [candidate["slug"] for candidate in workspace["candidates"]] == ["langgraph"]
    assert [criterion["name"] for criterion in workspace["criteria"]] == [
        "Runtime control and state"
    ]
    assert [recommendation["summary"] for recommendation in workspace["recommendations"]] == [
        "Recommended: LangGraph."
    ]
