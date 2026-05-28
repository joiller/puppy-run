from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from puppyrun_api.models import AgentRunStatus, DecisionSessionStatus


class CreateDecisionSessionRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=4000)


class DecisionSessionResponse(BaseModel):
    id: UUID
    title: str
    prompt: str
    status: DecisionSessionStatus
    workflow_stage: str
    decision_context: dict
    current_summary: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CreateDecisionMessageRequest(BaseModel):
    content: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=2, max_length=4000),
    ]


class DecisionMessageResponse(BaseModel):
    id: UUID
    session_id: UUID
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionCandidateResponse(BaseModel):
    id: UUID
    session_id: UUID
    name: str
    slug: str
    repo_full_name: str
    include_reason: str
    health_summary: str | None
    health_metrics: dict
    score: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionCriterionResponse(BaseModel):
    id: UUID
    session_id: UUID
    name: str
    weight: int
    rationale: str
    evidence_needed: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EvidenceItemResponse(BaseModel):
    id: UUID
    session_id: UUID
    candidate_id: UUID | None
    criterion_id: UUID | None
    source_type: str
    source_url: str
    title: str
    summary: str
    credibility: str
    payload: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class RecommendationResponse(BaseModel):
    id: UUID
    session_id: UUID
    recommended_candidate_id: UUID | None
    summary: str
    rationale: dict
    created_at: datetime

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


class WorkspaceResponse(BaseModel):
    session: DecisionSessionResponse
    messages: list[DecisionMessageResponse]
    candidates: list[DecisionCandidateResponse]
    criteria: list[DecisionCriterionResponse]
    evidence_items: list[EvidenceItemResponse]
    recommendations: list[RecommendationResponse]
    events: list[AgentEventResponse]
