import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_api.db import Base
from puppyrun_api.models import DecisionSession, DecisionSessionStatus
from puppyrun_api.repositories.sessions import (
    create_agent_run,
    create_decision_session,
)
from puppyrun_api.repositories.workspace import append_user_message
from puppyrun_worker import jobs
from puppyrun_worker.main import WorkerSettings


@pytest.mark.asyncio
async def test_phase1_agent_job_marks_session_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(jobs, "SessionLocal", maker)

    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph, OpenAI Agents SDK, and CrewAI for PuppyRun.",
        )
        await append_user_message(
            db,
            session.id,
            "We need Python, checkpointing, human approval, and observability.",
        )
        run = await create_agent_run(db, session.id)
        run_id = run.id
        session_id = session.id

    async def fake_workflow(db, run_id_arg):
        assert str(run_id_arg) == str(run_id)
        refreshed = await db.get(DecisionSession, session_id)
        assert refreshed is not None
        refreshed.status = DecisionSessionStatus.completed
        refreshed.workflow_stage = "completed"
        refreshed.current_summary = "Recommended: LangGraph."
        await db.commit()
        return "Recommended: LangGraph."

    monkeypatch.setattr(jobs, "run_phase1_workflow", fake_workflow)

    summary = await jobs.run_phase1_agent_job({}, str(run_id))

    assert summary == "Recommended: LangGraph."

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase2_agent_job_runs_targeted_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    assert jobs.run_phase2_agent_job in WorkerSettings.functions

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(jobs, "SessionLocal", maker)

    async with maker() as db:
        session = await create_decision_session(
            db,
            "Compare LangGraph, OpenAI Agents SDK, and CrewAI for PuppyRun.",
        )
        run = await create_agent_run(db, session.id)
        run_id = run.id

    async def fake_workflow(db, run_id_arg):
        assert str(run_id_arg) == str(run_id)
        return "Recommended v2: OpenAI Agents SDK."

    monkeypatch.setattr(jobs, "run_phase2_workflow", fake_workflow)

    summary = await jobs.run_phase2_agent_job({}, str(run_id))

    assert summary == "Recommended v2: OpenAI Agents SDK."

    await engine.dispose()
