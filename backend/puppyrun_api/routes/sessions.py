from typing import Annotated
from uuid import UUID

from arq import create_pool
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_api.config import get_settings
from puppyrun_api.db import get_session
from puppyrun_api.repositories import sessions as session_repo
from puppyrun_api.schemas import (
    AgentRunResponse,
    CreateDecisionSessionRequest,
    DecisionSessionResponse,
    StartAgentRunResponse,
)
from puppyrun_worker.main import redis_settings_from_url

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=DecisionSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateDecisionSessionRequest,
    db: SessionDep,
) -> DecisionSessionResponse:
    session = await session_repo.create_decision_session(db, body.prompt)
    return DecisionSessionResponse.model_validate(session)


@router.get("", response_model=list[DecisionSessionResponse])
async def list_sessions(db: SessionDep) -> list[DecisionSessionResponse]:
    sessions = await session_repo.list_decision_sessions(db)
    return [DecisionSessionResponse.model_validate(session) for session in sessions]


@router.get("/{session_id}", response_model=DecisionSessionResponse)
async def get_session_by_id(
    session_id: UUID,
    db: SessionDep,
) -> DecisionSessionResponse:
    session = await session_repo.get_decision_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="decision session not found")
    return DecisionSessionResponse.model_validate(session)


@router.post("/{session_id}/runs", response_model=StartAgentRunResponse, status_code=202)
async def start_dummy_run(
    session_id: UUID,
    db: SessionDep,
) -> StartAgentRunResponse:
    session = await session_repo.get_decision_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="decision session not found")

    run = await session_repo.create_agent_run(db, session_id)
    redis = await create_pool(redis_settings_from_url(get_settings().redis_url))
    try:
        job = await redis.enqueue_job("run_dummy_agent_job", str(run.id), _job_id=f"dummy:{run.id}")
        run.job_id = job.job_id if job is not None else f"dummy:{run.id}"
    finally:
        await redis.close()
    await db.commit()
    await db.refresh(run)
    await db.refresh(session)
    return StartAgentRunResponse(
        session=DecisionSessionResponse.model_validate(session),
        run=AgentRunResponse.model_validate(run),
    )
