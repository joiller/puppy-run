from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from puppyrun_api.db import Base
from puppyrun_api.models import (
    AgentEvent,
    AgentRun,
    DecisionCandidate,
    DecisionCriterion,
    DecisionMessage,
    DecisionSession,
    EvidenceItem,
    Recommendation,
)


@pytest.mark.asyncio
async def test_phase1_session_can_store_messages_and_stage() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session = DecisionSession(
            title="Compare Agent frameworks",
            prompt="Compare LangGraph and OpenAI Agents SDK for a web Agent runtime.",
            workflow_stage="clarifying",
            decision_context={"domain": "agent_framework_selection"},
        )
        session.messages.append(
            DecisionMessage(
                role="assistant",
                content="Which constraints matter most for the runtime?",
                created_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            )
        )
        session.messages.append(
            DecisionMessage(
                role="user",
                content="Stateful runtime support matters most.",
                created_at=datetime(2026, 5, 27, 12, 1, tzinfo=UTC),
            )
        )
        db.add(session)
        await db.commit()

    async with maker() as db:
        stored = await db.scalar(
            select(DecisionSession).options(selectinload(DecisionSession.messages))
        )
        assert stored is not None
        assert stored.workflow_stage == "clarifying"
        assert stored.decision_context["domain"] == "agent_framework_selection"
        assert [message.role for message in stored.messages] == ["assistant", "user"]
        messages = (await db.execute(select(DecisionMessage))).scalars().all()
        assert sorted(message.role for message in messages) == ["assistant", "user"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase1_messages_are_ordered_by_created_at() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session = DecisionSession(
            title="Compare Agent frameworks",
            prompt="Compare LangGraph and OpenAI Agents SDK for a web Agent runtime.",
        )
        session.messages.append(
            DecisionMessage(
                role="assistant",
                content="Second message",
                created_at=datetime(2026, 5, 27, 12, 1, tzinfo=UTC),
            )
        )
        session.messages.append(
            DecisionMessage(
                role="user",
                content="First message",
                created_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            )
        )
        db.add(session)
        await db.commit()
        session_id = session.id

    async with maker() as db:
        stored = await db.scalar(
            select(DecisionSession)
            .where(DecisionSession.id == session_id)
            .options(selectinload(DecisionSession.messages))
        )
        assert stored is not None
        assert [message.content for message in stored.messages] == [
            "First message",
            "Second message",
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase1_session_tracks_decision_context_in_place_updates() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session = DecisionSession(
            title="Compare Agent frameworks",
            prompt="Compare LangGraph and OpenAI Agents SDK for a web Agent runtime.",
            decision_context={"domain": "agent_framework_selection"},
        )
        db.add(session)
        await db.commit()
        session_id = session.id

    async with maker() as db:
        stored = await db.get(DecisionSession, session_id)
        assert stored is not None
        stored.decision_context["priority"] = "runtime_fit"
        await db.commit()

    async with maker() as db:
        stored = await db.get(DecisionSession, session_id)
        assert stored is not None
        assert stored.decision_context["priority"] == "runtime_fit"

    await engine.dispose()


@pytest.mark.asyncio
async def test_phase1_session_tracks_nested_decision_context_in_place_updates() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        session = DecisionSession(
            title="Compare Agent frameworks",
            prompt="Compare LangGraph and OpenAI Agents SDK for a web Agent runtime.",
            decision_context={
                "domain": "agent_framework_selection",
                "constraints": {"runtime": "web"},
            },
        )
        db.add(session)
        await db.commit()
        session_id = session.id

    async with maker() as db:
        stored = await db.get(DecisionSession, session_id)
        assert stored is not None
        stored.decision_context["constraints"]["runtime"] = "agent_runtime"
        await db.commit()

    async with maker() as db:
        stored = await db.get(DecisionSession, session_id)
        assert stored is not None
        assert stored.decision_context["constraints"]["runtime"] == "agent_runtime"

    await engine.dispose()


def _ondelete(model: type, column_name: str) -> str | None:
    foreign_key = next(iter(model.__table__.c[column_name].foreign_keys))
    return foreign_key.ondelete


def test_phase1_model_foreign_keys_match_migration_ondelete() -> None:
    assert _ondelete(AgentRun, "session_id") == "CASCADE"
    assert _ondelete(AgentEvent, "run_id") == "CASCADE"
    assert _ondelete(DecisionMessage, "session_id") == "CASCADE"
    assert _ondelete(DecisionCandidate, "session_id") == "CASCADE"
    assert _ondelete(DecisionCriterion, "session_id") == "CASCADE"
    assert _ondelete(EvidenceItem, "session_id") == "CASCADE"
    assert _ondelete(EvidenceItem, "candidate_id") == "SET NULL"
    assert _ondelete(EvidenceItem, "criterion_id") == "SET NULL"
    assert _ondelete(Recommendation, "session_id") == "CASCADE"
    assert _ondelete(Recommendation, "recommended_candidate_id") == "SET NULL"
