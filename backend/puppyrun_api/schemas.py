from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from puppyrun_api.models import AgentRunStatus, DecisionSessionStatus, DecisionVersionStatus


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


class DecisionVersionResponse(BaseModel):
    id: UUID
    session_id: UUID
    version_number: int
    label: str
    status: DecisionVersionStatus
    source_version_id: UUID | None
    change_summary: dict
    gap_analysis: dict
    adr: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class DecisionCandidateResponse(BaseModel):
    id: UUID
    session_id: UUID
    decision_version_id: UUID | None
    name: str
    slug: str
    repo_full_name: str
    include_reason: str
    health_summary: str | None
    health_metrics: dict
    score: int | None
    selection_state: str
    is_locked: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionCriterionResponse(BaseModel):
    id: UUID
    session_id: UUID
    decision_version_id: UUID | None
    name: str
    weight: int
    rationale: str
    evidence_needed: str
    is_locked: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class EvidenceItemResponse(BaseModel):
    id: UUID
    session_id: UUID
    decision_version_id: UUID | None
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
    decision_version_id: UUID | None
    recommended_candidate_id: UUID | None
    summary: str
    rationale: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class ScoreCellResponse(BaseModel):
    id: UUID
    session_id: UUID
    decision_version_id: UUID
    candidate_id: UUID
    criterion_id: UUID
    score: int
    status: str
    explanation: str
    evidence_item_ids: list[UUID]
    created_at: datetime

    model_config = {"from_attributes": True}


class Phase2DraftResponse(BaseModel):
    source_version_id: UUID | None = None
    candidate_overrides: dict = Field(default_factory=dict)
    custom_candidates: dict = Field(default_factory=dict)
    must_include_constraints: dict = Field(default_factory=dict)
    must_exclude_constraints: dict = Field(default_factory=dict)
    weight_overrides: dict = Field(default_factory=dict)


class GapAnalysisResponse(BaseModel):
    requires_research: bool = False
    score_only: bool = False
    changed_candidates: list[str] = Field(default_factory=list)
    changed_constraints: list[str] = Field(default_factory=list)
    changed_weights: list[str] = Field(default_factory=list)
    research_tasks: list[dict] = Field(default_factory=list)
    reuse_tasks: list[dict] = Field(default_factory=list)
    items: list[dict] = Field(default_factory=list)


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
    versions: list[DecisionVersionResponse]
    active_version: DecisionVersionResponse | None
    draft: Phase2DraftResponse
    gap_analysis: GapAnalysisResponse
    candidates: list[DecisionCandidateResponse]
    criteria: list[DecisionCriterionResponse]
    evidence_items: list[EvidenceItemResponse]
    score_cells: list[ScoreCellResponse]
    recommendations: list[RecommendationResponse]
    events: list[AgentEventResponse]
