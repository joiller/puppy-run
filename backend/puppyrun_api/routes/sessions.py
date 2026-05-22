from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_api.db import get_session
from puppyrun_api.repositories import sessions as session_repo
from puppyrun_api.schemas import CreateDecisionSessionRequest, DecisionSessionResponse

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", response_model=DecisionSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateDecisionSessionRequest,
    db: AsyncSession = Depends(get_session),
) -> DecisionSessionResponse:
    session = await session_repo.create_decision_session(db, body.prompt)
    return DecisionSessionResponse.model_validate(session)


@router.get("", response_model=list[DecisionSessionResponse])
async def list_sessions(db: AsyncSession = Depends(get_session)) -> list[DecisionSessionResponse]:
    sessions = await session_repo.list_decision_sessions(db)
    return [DecisionSessionResponse.model_validate(session) for session in sessions]


@router.get("/{session_id}", response_model=DecisionSessionResponse)
async def get_session_by_id(
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> DecisionSessionResponse:
    session = await session_repo.get_decision_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="decision session not found")
    return DecisionSessionResponse.model_validate(session)
