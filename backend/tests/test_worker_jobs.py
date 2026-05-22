import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_api.db import Base
from puppyrun_api.models import DecisionSessionStatus
from puppyrun_api.repositories.sessions import create_agent_run, create_decision_session
from puppyrun_worker import jobs


@pytest.mark.asyncio
async def test_dummy_agent_job_marks_session_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(jobs, "SessionLocal", maker)

    async with maker() as db:
        session = await create_decision_session(
            db, "Compare LangGraph and OpenAI Agents SDK for PuppyRun."
        )
        run = await create_agent_run(db, session.id)
        run_id = run.id
        session_id = session.id

    await jobs.run_dummy_agent_job({}, str(run_id))

    async with maker() as db:
        refreshed = await db.get(type(session), session_id)
        assert refreshed is not None
        assert refreshed.status == DecisionSessionStatus.completed
        assert refreshed.current_summary == (
            "Phase 0 dummy Agent completed. Real research workflow is not enabled yet."
        )

    await engine.dispose()
