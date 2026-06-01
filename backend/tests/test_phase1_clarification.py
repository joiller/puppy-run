from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from puppyrun_agent.catalog import select_candidates
from puppyrun_agent.clarification import (
    build_initial_context,
    build_initial_question,
    update_context_with_answer,
)
from puppyrun_agent.criteria import generate_criteria
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


def test_build_initial_context_detects_agent_framework_domain() -> None:
    context = build_initial_context(
        "Should I use CrewAI, LangGraph, or OpenAI Agents SDK for a web Agent runtime?"
    )

    assert context["domain"] == "agent_framework_selection"
    assert context["mentioned_candidates"] == ["crewai", "langgraph", "openai_agents_sdk"]
    assert context["clarification_turns"] == 0


def test_build_initial_context_detects_constraints_and_language() -> None:
    context = build_initial_context(
        "Compare LangGraph and CrewAI with checkpoint state, human approval, "
        "trace observability, and TypeScript support."
    )

    assert set(context["constraints"]) == {
        "checkpointing",
        "stateful_runtime",
        "human_in_loop",
        "observability",
        "typescript",
    }
    assert context["language_preference"] == "typescript"


def test_build_initial_question_is_specific_to_missing_constraints() -> None:
    context = build_initial_context("Compare LangGraph and CrewAI for a web Agent runtime.")

    question = build_initial_question(context)

    assert "constraints matter most" in question
    assert "checkpointing" in question
    assert "human approval" in question


def test_update_context_with_answer_merges_constraints_and_increments_turns() -> None:
    context = {
        "domain": "agent_framework_selection",
        "mentioned_candidates": ["langgraph"],
        "constraints": ["checkpointing"],
        "language_preference": "typescript",
        "clarification_turns": 0,
    }

    updated = update_context_with_answer(
        context,
        "We need Python support, human approval gates, and production tracing.",
    )

    assert set(updated["constraints"]) == {
        "checkpointing",
        "human_in_loop",
        "observability",
        "python",
    }
    assert updated["language_preference"] == "python"
    assert updated["clarification_turns"] == 1


def test_select_candidates_limits_phase1_to_three_candidates() -> None:
    context = build_initial_context(
        "Compare OpenAI Agents SDK, CrewAI, LangGraph, and AutoGen for a web Agent runtime."
    )

    candidates = select_candidates(context)

    assert [candidate.slug for candidate in candidates] == [
        "openai_agents_sdk",
        "crewai",
        "langgraph",
    ]
    assert [candidate.repo_full_name for candidate in candidates] == [
        "openai/openai-agents-python",
        "crewAIInc/crewAI",
        "langchain-ai/langgraph",
    ]


def test_generate_criteria_weights_agent_runtime_needs() -> None:
    context = {
        "constraints": ["checkpointing", "human_in_loop", "observability"],
        "language_preference": "python",
    }

    criteria = generate_criteria(context)

    assert len(criteria) == 5
    assert criteria[0].name == "Runtime control and state"
    assert sum(criterion.weight for criterion in criteria) == 100
