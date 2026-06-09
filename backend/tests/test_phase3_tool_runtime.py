import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_agent.tool_runtime import RegisteredTool, ToolContext, ToolResult, ToolRuntime
from puppyrun_api.config import get_settings
from puppyrun_api.db import Base
from puppyrun_api.models import DecisionVersion, ToolCall
from puppyrun_api.repositories.sessions import create_decision_session


async def _seed_version(maker) -> tuple:
    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph and CrewAI for a stateful Agent runtime.",
        )
        version = DecisionVersion(
            session_id=session.id,
            version_number=1,
            label="Phase 1 baseline",
            status="running",
            change_summary={"kind": "phase1"},
            gap_analysis={"items": []},
        )
        db.add(version)
        await db.commit()
        return session.id, version.id


@pytest.fixture
async def runtime_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_tool_runtime_persists_completed_call_and_deduplicates(runtime_db) -> None:
    session_id, version_id = await _seed_version(runtime_db)

    async def handler(_context: ToolContext, _inputs: dict) -> ToolResult:
        return ToolResult(
            status="completed",
            source_type="official_docs",
            source_url="https://example.com/docs",
            request_summary="Fetch docs.",
            response_summary="Fetched docs.",
            payload={"result_count": 1},
        )

    async with runtime_db() as db:
        runtime = ToolRuntime(db, session_id=session_id, decision_version_id=version_id)
        runtime.register(RegisteredTool(name="direct_docs", handler=handler))

        first = await runtime.execute("direct_docs", {"url": "https://example.com/docs"})
        second = await runtime.execute("direct_docs", {"url": "https://example.com/docs"})
        calls = (await db.execute(select(ToolCall))).scalars().all()

    assert first.status == "completed"
    assert second.status == "completed"
    assert len(calls) == 1
    assert calls[0].status == "completed"
    assert calls[0].tool_name == "direct_docs"
    assert calls[0].source_type == "official_docs"
    assert calls[0].source_url == "https://example.com/docs"
    assert calls[0].request_summary == "Fetch docs."
    assert calls[0].response_summary == "Fetched docs."
    assert calls[0].payload == {"result_count": 1}
    assert calls[0].started_at is not None
    assert calls[0].completed_at is not None


@pytest.mark.asyncio
async def test_tool_runtime_sanitizes_result_metadata(runtime_db) -> None:
    session_id, version_id = await _seed_version(runtime_db)

    async def handler(_context: ToolContext, _inputs: dict) -> ToolResult:
        return ToolResult(
            status="completed",
            source_type="docs",
            source_url=(
                "https://example.com/docs?api_key=secret-api-key"
                "&access_token=secret-access-token"
                "&refresh_token=secret-refresh-token"
            ),
            request_summary="Authorization: Bearer secret-token-value",
            response_summary=(
                "Fetched with token=secret-token-value access_token=secret-access-token "
                "client_secret=secret-client-secret password=secret-password"
            ),
            payload={"raw_content": "community post " * 200},
        )

    async with runtime_db() as db:
        runtime = ToolRuntime(db, session_id=session_id, decision_version_id=version_id)
        runtime.register(RegisteredTool(name="metadata_tool", handler=handler))

        result = await runtime.execute("metadata_tool", {"url": "https://example.com/docs"})
        call = (await db.execute(select(ToolCall))).scalar_one()

    assert "secret-api-key" not in (result.source_url or "")
    assert "secret-access-token" not in (result.source_url or "")
    assert "secret-refresh-token" not in (result.source_url or "")
    assert "secret-token-value" not in (result.request_summary or "")
    assert "secret-token-value" not in (result.response_summary or "")
    assert "secret-access-token" not in (result.response_summary or "")
    assert "secret-client-secret" not in (result.response_summary or "")
    assert "secret-password" not in (result.response_summary or "")
    assert "secret-api-key" not in (call.source_url or "")
    assert "secret-access-token" not in (call.source_url or "")
    assert "secret-refresh-token" not in (call.source_url or "")
    assert "secret-token-value" not in (call.request_summary or "")
    assert "secret-token-value" not in (call.response_summary or "")
    assert "secret-access-token" not in (call.response_summary or "")
    assert "secret-client-secret" not in (call.response_summary or "")
    assert "secret-password" not in (call.response_summary or "")
    assert call.payload["raw_content"] == "[redacted raw content]"


@pytest.mark.asyncio
async def test_tool_runtime_idempotency_uses_untruncated_inputs(runtime_db) -> None:
    session_id, version_id = await _seed_version(runtime_db)

    async def handler(_context: ToolContext, inputs: dict) -> ToolResult:
        return ToolResult(status="completed", payload={"query_length": len(inputs["query"])})

    async with runtime_db() as db:
        runtime = ToolRuntime(db, session_id=session_id, decision_version_id=version_id)
        runtime.register(RegisteredTool(name="long_query_tool", handler=handler))

        await runtime.execute("long_query_tool", {"query": f"{'x' * 1000}a"})
        await runtime.execute("long_query_tool", {"query": f"{'x' * 1000}b"})
        calls = (await db.execute(select(ToolCall))).scalars().all()

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_tool_runtime_duplicate_insert_race_returns_running_call_as_skipped(
    runtime_db,
) -> None:
    session_id, version_id = await _seed_version(runtime_db)
    handler_calls = 0

    async def handler(_context: ToolContext, _inputs: dict) -> ToolResult:
        nonlocal handler_calls
        handler_calls += 1
        return ToolResult(status="completed", payload={"unexpected": True})

    class RaceRuntime(ToolRuntime):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.lookup_count = 0

        async def _get_existing_call(self, idempotency_key: str):
            self.lookup_count += 1
            if self.lookup_count == 1:
                return None
            return await super()._get_existing_call(idempotency_key)

    async with runtime_db() as db:
        runtime = RaceRuntime(db, session_id=session_id, decision_version_id=version_id)
        runtime.register(RegisteredTool(name="race_tool", handler=handler))
        idempotency_key = runtime._idempotency_key("race_tool", {"query": "same"}, None)
        db.add(
            ToolCall(
                session_id=session_id,
                decision_version_id=version_id,
                tool_name="race_tool",
                status="running",
                idempotency_key=idempotency_key,
                payload={"inputs": {"query": "same"}},
            )
        )
        await db.commit()

        result = await runtime.execute("race_tool", {"query": "same"})
        calls = (await db.execute(select(ToolCall))).scalars().all()

    assert handler_calls == 0
    assert len(calls) == 1
    assert result.status == "skipped"
    assert result.payload == {"reason": "duplicate_call_running"}


@pytest.mark.asyncio
async def test_tool_runtime_persists_skipped_call(runtime_db) -> None:
    session_id, version_id = await _seed_version(runtime_db)

    async def handler(_context: ToolContext, _inputs: dict) -> ToolResult:
        return ToolResult(
            status="skipped",
            source_type="tavily_search",
            request_summary="Search external sources.",
            response_summary="Skipped because Tavily credentials are missing.",
            payload={"reason": "missing_credentials"},
        )

    async with runtime_db() as db:
        runtime = ToolRuntime(db, session_id=session_id, decision_version_id=version_id)
        runtime.register(RegisteredTool(name="tavily_search", handler=handler))

        result = await runtime.execute("tavily_search", {"query": "LangGraph maintenance"})
        call = (await db.execute(select(ToolCall))).scalar_one()

    assert result.status == "skipped"
    assert call.status == "skipped"
    assert call.error is None
    assert call.payload == {"reason": "missing_credentials"}


@pytest.mark.asyncio
async def test_tool_runtime_retries_transient_failure_once(runtime_db) -> None:
    session_id, version_id = await _seed_version(runtime_db)
    attempts = 0

    async def handler(_context: ToolContext, _inputs: dict) -> ToolResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary upstream failure")
        return ToolResult(status="completed", response_summary="Recovered.")

    async with runtime_db() as db:
        runtime = ToolRuntime(
            db,
            session_id=session_id,
            decision_version_id=version_id,
            retry_count=1,
        )
        runtime.register(RegisteredTool(name="retry_tool", handler=handler))

        result = await runtime.execute("retry_tool", {"query": "retry"})
        call = (await db.execute(select(ToolCall))).scalar_one()

    assert attempts == 2
    assert result.status == "completed"
    assert call.status == "completed"
    assert call.error is None
    assert call.response_summary == "Recovered."


@pytest.mark.asyncio
async def test_tool_runtime_persists_failed_call_with_sanitized_payload(runtime_db) -> None:
    session_id, version_id = await _seed_version(runtime_db)

    async def handler(_context: ToolContext, _inputs: dict) -> ToolResult:
        raise RuntimeError(
            "Authorization: Bearer secret-token-value; "
            "upstream failed with token=secret-token-value api_key=secret-api-key"
        )

    async with runtime_db() as db:
        runtime = ToolRuntime(
            db,
            session_id=session_id,
            decision_version_id=version_id,
            retry_count=0,
        )
        runtime.register(RegisteredTool(name="unsafe_tool", handler=handler))

        result = await runtime.execute(
            "unsafe_tool",
            {
                "Authorization": "Bearer secret-token-value",
                "api_key": "secret-api-key",
                "nested": {"token": "secret-token-value", "safe": "kept"},
                "note": "do not persist token=secret-token-value or api_key=secret-api-key",
                "raw_content": "community post " * 200,
            },
        )
        call = (await db.execute(select(ToolCall))).scalar_one()

    assert result.status == "failed"
    assert call.status == "failed"
    assert "secret-token-value" not in (call.error or "")
    assert "secret-api-key" not in (call.error or "")
    assert call.payload["inputs"]["Authorization"] == "[redacted]"
    assert call.payload["inputs"]["api_key"] == "[redacted]"
    assert call.payload["inputs"]["nested"] == {"token": "[redacted]", "safe": "kept"}
    assert "secret-token-value" not in call.payload["inputs"]["note"]
    assert "secret-api-key" not in call.payload["inputs"]["note"]
    assert call.payload["inputs"]["raw_content"] == "[redacted raw content]"


@pytest.mark.asyncio
async def test_tool_runtime_defaults_to_configured_retry_count(
    runtime_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, version_id = await _seed_version(runtime_db)
    monkeypatch.setenv("PUPPYRUN_TOOL_RETRY_COUNT", "0")
    get_settings.cache_clear()
    attempts = 0

    async def handler(_context: ToolContext, _inputs: dict) -> ToolResult:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("upstream failed")

    try:
        async with runtime_db() as db:
            runtime = ToolRuntime(
                db,
                session_id=session_id,
                decision_version_id=version_id,
            )
            runtime.register(RegisteredTool(name="configured_tool", handler=handler))

            result = await runtime.execute("configured_tool", {"query": "config"})

        assert result.status == "failed"
        assert attempts == 1
    finally:
        get_settings.cache_clear()
