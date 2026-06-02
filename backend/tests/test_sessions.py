import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_api.db import Base, get_session
from puppyrun_api.main import create_app


@pytest.fixture
async def session_client() -> AsyncClient:
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
        yield client
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_get_session(session_client: AsyncClient) -> None:
    create_response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime."},
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["status"] == "created"
    assert created["title"].startswith("Compare LangGraph")

    get_response = await session_client.get(f"/api/v1/sessions/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_create_session_rejects_short_prompt(session_client: AsyncClient) -> None:
    response = await session_client.post("/api/v1/sessions", json={"prompt": "short"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_session_returns_initial_clarification(session_client: AsyncClient) -> None:
    response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and CrewAI for a web Agent runtime."},
    )

    assert response.status_code == 201
    created = response.json()
    assert created["workflow_stage"] == "clarifying"
    assert created["decision_context"]["domain"] == "agent_framework_selection"

    workspace_response = await session_client.get(f"/api/v1/sessions/{created['id']}/workspace")
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["session"]["id"] == created["id"]
    assert workspace["messages"][0]["role"] == "assistant"
    assert "constraints matter most" in workspace["messages"][0]["content"]


@pytest.mark.asyncio
async def test_answer_clarification_marks_session_ready(session_client: AsyncClient) -> None:
    create_response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a web Agent runtime."},
    )
    session_id = create_response.json()["id"]

    answer_response = await session_client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={
            "content": (
                "We need Python first, durable checkpoints, human approval steps, "
                "and simple production tracing."
            )
        },
    )

    assert answer_response.status_code == 201
    workspace = answer_response.json()
    assert workspace["session"]["workflow_stage"] == "ready_for_research"
    assert [message["role"] for message in workspace["messages"]] == ["assistant", "user"]


@pytest.mark.asyncio
async def test_get_missing_session_workspace_returns_404(session_client: AsyncClient) -> None:
    response = await session_client.get(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000001/workspace"
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_post_missing_session_message_returns_404(session_client: AsyncClient) -> None:
    response = await session_client.post(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000001/messages",
        json={"content": "Durable checkpoints matter most."},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_post_session_message_rejects_too_short_content(
    session_client: AsyncClient,
) -> None:
    create_response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a web Agent runtime."},
    )
    session_id = create_response.json()["id"]

    response = await session_client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "x"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_session_message_rejects_whitespace_only_content(
    session_client: AsyncClient,
) -> None:
    create_response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a web Agent runtime."},
    )
    session_id = create_response.json()["id"]

    response = await session_client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "  \n  "},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_workspace_persists_stripped_clarification_answer(
    session_client: AsyncClient,
) -> None:
    create_response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a web Agent runtime."},
    )
    session_id = create_response.json()["id"]

    answer_response = await session_client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"content": "  Durable checkpoints and simple tracing. \n"},
    )
    assert answer_response.status_code == 201

    workspace_response = await session_client.get(f"/api/v1/sessions/{session_id}/workspace")

    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["session"]["workflow_stage"] == "ready_for_research"
    assert workspace["messages"][1]["content"] == "Durable checkpoints and simple tracing."
    assert (
        workspace["session"]["decision_context"]["clarification"]["answer"]
        == "Durable checkpoints and simple tracing."
    )


@pytest.mark.asyncio
async def test_start_run_enqueues_phase1_job(
    session_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a web Agent runtime."},
    )
    session_id = response.json()["id"]

    class FakeJob:
        job_id = "phase1:test-job"

    class FakeRedis:
        async def enqueue_job(self, name: str, run_id: str, _job_id: str):
            assert name == "run_phase1_agent_job"
            assert _job_id.startswith("phase1:")
            return FakeJob()

        async def close(self) -> None:
            return None

    async def fake_create_pool(settings):
        return FakeRedis()

    monkeypatch.setattr("puppyrun_api.routes.sessions.create_pool", fake_create_pool)

    run_response = await session_client.post(f"/api/v1/sessions/{session_id}/runs")

    assert run_response.status_code == 202
    assert run_response.json()["run"]["job_id"] == "phase1:test-job"
