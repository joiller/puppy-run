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
