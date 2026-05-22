from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from puppyrun_api.models import AgentRunStatus, DecisionSessionStatus


class CreateDecisionSessionRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=4000)


class DecisionSessionResponse(BaseModel):
    id: UUID
    title: str
    prompt: str
    status: DecisionSessionStatus
    current_summary: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentRunResponse(BaseModel):
    id: UUID
    session_id: UUID
    status: AgentRunStatus
    job_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentEventResponse(BaseModel):
    id: UUID
    run_id: UUID
    event_type: str
    message: str
    payload: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class StartAgentRunResponse(BaseModel):
    session: DecisionSessionResponse
    run: AgentRunResponse
