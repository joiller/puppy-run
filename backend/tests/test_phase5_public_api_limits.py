import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_api import models
from puppyrun_api.config import get_settings
from puppyrun_api.db import Base, get_session
from puppyrun_api.main import create_app
from puppyrun_api.repositories.sessions import create_decision_session


class FakeJob:
    job_id = "phase5:test-job"


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}
        self.enqueue_failures = 0

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value) -> None:
        self.values[key] = value

    async def incr(self, key: str) -> int:
        self.values[key] = int(self.values.get(key, 0)) + 1
        return int(self.values[key])

    async def decr(self, key: str) -> int:
        self.values[key] = int(self.values.get(key, 0)) - 1
        return int(self.values[key])

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def enqueue_job(self, name: str, run_id: str, _job_id: str):
        if self.enqueue_failures > 0:
            self.enqueue_failures -= 1
            raise RuntimeError("redis enqueue failed")
        return FakeJob()

    async def close(self) -> None:
        return None


@pytest.fixture
async def phase5_client(monkeypatch: pytest.MonkeyPatch):
    fake_redis = FakeRedis()
    monkeypatch.setenv("PUPPYRUN_DEMO_SAFETY_ENABLED", "true")
    monkeypatch.setenv("PUPPYRUN_LIVE_DEMO_ENABLED", "true")
    monkeypatch.setenv("PUPPYRUN_LIVE_RUN_DAILY_LIMIT", "1")
    monkeypatch.setenv("PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP", "1")
    monkeypatch.setenv("PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP", "1")
    monkeypatch.setenv("PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP", "2")
    get_settings.cache_clear()

    async def fake_create_pool(settings):
        return fake_redis

    monkeypatch.setattr("puppyrun_api.routes.sessions.create_pool", fake_create_pool)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client, fake_redis, maker

    get_settings.cache_clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_session_create_daily_ip_quota_returns_structured_429(phase5_client) -> None:
    client, _redis, _maker = phase5_client

    first = await client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime."},
    )
    second = await client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare CrewAI and AutoGen for a stateful Agent runtime."},
    )

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["code"] == "session_create_daily_limit_exceeded"
    assert second.json()["limit"] == 1
    assert second.json()["remaining"] == 0


@pytest.mark.asyncio
async def test_live_run_quota_blocks_second_run_with_structured_429(phase5_client) -> None:
    client, _redis, _maker = phase5_client
    create = await client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime."},
    )
    session_id = create.json()["id"]

    first = await client.post(f"/api/v1/sessions/{session_id}/runs")
    second = await client.post(f"/api/v1/sessions/{session_id}/runs")

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["code"] == "live_run_daily_limit_exceeded"


@pytest.mark.asyncio
async def test_missing_session_run_does_not_consume_live_quota(phase5_client) -> None:
    client, _redis, _maker = phase5_client

    missing = await client.post("/api/v1/sessions/00000000-0000-0000-0000-000000000001/runs")
    create = await client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime."},
    )
    allowed = await client.post(f"/api/v1/sessions/{create.json()['id']}/runs")

    assert missing.status_code == 404
    assert allowed.status_code == 202


@pytest.mark.asyncio
async def test_disabled_live_demo_blocks_new_run(phase5_client) -> None:
    client, fake_redis, _maker = phase5_client
    fake_redis.values["puppyrun:demo:live_enabled"] = "false"
    create = await client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime."},
    )

    response = await client.post(f"/api/v1/sessions/{create.json()['id']}/runs")

    assert response.status_code == 403
    assert response.json()["code"] == "live_demo_disabled"


@pytest.mark.asyncio
async def test_read_rate_limit_keeps_error_payload_top_level(phase5_client) -> None:
    client, _redis, _maker = phase5_client

    first = await client.get("/api/v1/sessions")
    second = await client.get("/api/v1/sessions")
    third = await client.get("/api/v1/sessions")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["code"] == "read_rate_limit_exceeded"


@pytest.mark.asyncio
async def test_phase2_missing_session_does_not_consume_live_quota(phase5_client) -> None:
    client, _redis, maker = phase5_client

    missing = await client.post(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000001/versions"
    )
    async with maker() as db:
        session = await _create_phase2_ready_session(db)
        session_id = session.id

    allowed = await client.post(f"/api/v1/sessions/{session_id}/versions")

    assert missing.status_code == 404
    assert allowed.status_code == 202


@pytest.mark.asyncio
async def test_phase2_conflict_does_not_consume_live_quota(phase5_client) -> None:
    client, _redis, maker = phase5_client
    async with maker() as db:
        no_draft_session = await create_decision_session(
            db,
            "Compare LangGraph and CrewAI for a stateful Agent runtime.",
        )
        ready_session = await _create_phase2_ready_session(db)
        no_draft_session_id = no_draft_session.id
        ready_session_id = ready_session.id

    conflict = await client.post(f"/api/v1/sessions/{no_draft_session_id}/versions")
    allowed = await client.post(f"/api/v1/sessions/{ready_session_id}/versions")

    assert conflict.status_code == 409
    assert allowed.status_code == 202


@pytest.mark.asyncio
async def test_phase2_enqueue_failure_rolls_back_live_quota(phase5_client) -> None:
    client, fake_redis, maker = phase5_client
    async with maker() as db:
        failing_session = await _create_phase2_ready_session(
            db,
            "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime.",
        )
        allowed_session = await _create_phase2_ready_session(
            db,
            "Compare CrewAI and AutoGen for a stateful Agent runtime.",
        )
        failing_session_id = failing_session.id
        allowed_session_id = allowed_session.id

    fake_redis.enqueue_failures = 1
    failed = await client.post(f"/api/v1/sessions/{failing_session_id}/versions")
    allowed = await client.post(f"/api/v1/sessions/{allowed_session_id}/versions")

    assert failed.status_code == 503
    assert failed.json()["detail"] == "failed to enqueue phase2 run"
    assert allowed.status_code == 202


@pytest.mark.asyncio
async def test_phase1_enqueue_failure_marks_failed_and_rolls_back_live_quota(
    phase5_client,
) -> None:
    client, fake_redis, maker = phase5_client
    async with maker() as db:
        failing_session = await create_decision_session(
            db,
            "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime.",
        )
        allowed_session = await create_decision_session(
            db,
            "Compare CrewAI and AutoGen for a stateful Agent runtime.",
        )
        failing_session_id = failing_session.id
        allowed_session_id = allowed_session.id

    fake_redis.enqueue_failures = 1
    failed = await client.post(f"/api/v1/sessions/{failing_session_id}/runs")
    allowed = await client.post(f"/api/v1/sessions/{allowed_session_id}/runs")

    assert failed.status_code == 503
    assert failed.json()["detail"] == "failed to enqueue agent run"
    assert allowed.status_code == 202

    async with maker() as db:
        run = (
            await db.execute(
                select(models.AgentRun).where(
                    models.AgentRun.session_id == failing_session_id
                )
            )
        ).scalar_one()
        session = await db.get(models.DecisionSession, failing_session_id)
        assert session is not None
        assert run.status == models.AgentRunStatus.failed
        assert run.job_id is None
        assert session.status == models.DecisionSessionStatus.failed
        assert session.workflow_stage == "failed"

        events = (
            await db.execute(
                select(models.AgentEvent)
                .where(models.AgentEvent.run_id == run.id)
                .order_by(models.AgentEvent.created_at.asc())
            )
        ).scalars().all()
        assert [event.event_type for event in events] == ["phase1_enqueue_failed"]
        assert events[0].payload == {
            "error": "redis enqueue failed",
            "error_type": "RuntimeError",
            "phase": "enqueue",
        }


async def _create_phase2_ready_session(
    db,
    prompt: str = "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime.",
):
    session = await create_decision_session(db, prompt)
    version = models.DecisionVersion(
        session_id=session.id,
        version_number=1,
        label="Phase 1 baseline",
        status=models.DecisionVersionStatus.completed,
        change_summary={"kind": "phase1_baseline"},
        gap_analysis={"items": []},
        adr="ADR v1: Recommended LangGraph.",
    )
    db.add(version)
    await db.flush()
    session.decision_context = {
        **session.decision_context,
        "phase2_draft": {
            "source_version_id": str(version.id),
            "candidate_overrides": {},
            "custom_candidates": {},
            "must_include_constraints": {},
            "must_exclude_constraints": {},
            "weight_overrides": {
                "Observability and traceability": {
                    "weight": 45,
                    "reason": "Traceability is the main driver.",
                }
            },
        },
    }
    await db.commit()
    return session
