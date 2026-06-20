from typing import Annotated
from uuid import UUID

from arq import create_pool
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_api.config import get_settings
from puppyrun_api.db import get_session
from puppyrun_api.demo_limits import (
    DemoSafetyPolicy,
    RedisDemoLimitStore,
    client_ip_from_request,
)
from puppyrun_api.repositories import sessions as session_repo
from puppyrun_api.repositories import workspace as workspace_repo
from puppyrun_api.schemas import (
    AgentEventResponse,
    AgentRunResponse,
    ClaimResponse,
    CreateDecisionMessageRequest,
    CreateDecisionSessionRequest,
    DecisionCandidateResponse,
    DecisionCriterionResponse,
    DecisionMessageResponse,
    DecisionSessionResponse,
    DecisionVersionResponse,
    EvidenceItemResponse,
    GapAnalysisResponse,
    Phase2DraftRequest,
    Phase2DraftResponse,
    RecommendationResponse,
    RiskSignalResponse,
    ScoreCellResponse,
    StartAgentRunResponse,
    ToolCallResponse,
    VerificationTaskResponse,
    WorkspaceResponse,
)
from puppyrun_worker.main import redis_settings_from_url

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _demo_safety_is_enabled() -> bool:
    return get_settings().demo_safety_enabled


def _demo_policy(redis) -> DemoSafetyPolicy:
    return DemoSafetyPolicy(settings=get_settings(), store=RedisDemoLimitStore(redis))


async def _open_redis():
    return await create_pool(redis_settings_from_url(get_settings().redis_url))


async def _check_read_rate(request: Request) -> None:
    if not _demo_safety_is_enabled():
        return
    redis = await _open_redis()
    try:
        await _demo_policy(redis).check_read_rate(
            client_ip_from_request(request, get_settings())
        )
    finally:
        await redis.close()


@router.post("", response_model=DecisionSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateDecisionSessionRequest,
    request: Request,
    db: SessionDep,
) -> DecisionSessionResponse:
    if not _demo_safety_is_enabled():
        session = await session_repo.create_decision_session(db, body.prompt)
        return DecisionSessionResponse.model_validate(session)

    redis = await _open_redis()
    policy = _demo_policy(redis)
    receipt = None
    try:
        receipt = await policy.consume_session_create(
            client_ip_from_request(request, get_settings())
        )
        session = await session_repo.create_decision_session(db, body.prompt)
        return DecisionSessionResponse.model_validate(session)
    except Exception:
        if receipt is not None:
            await policy.rollback_session_create(receipt)
        raise
    finally:
        await redis.close()


@router.get("", response_model=list[DecisionSessionResponse])
async def list_sessions(
    request: Request,
    db: SessionDep,
) -> list[DecisionSessionResponse]:
    await _check_read_rate(request)
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


@router.get("/{session_id}/workspace", response_model=WorkspaceResponse)
async def get_session_workspace(
    session_id: UUID,
    request: Request,
    db: SessionDep,
    version_id: UUID | None = None,
) -> WorkspaceResponse:
    await _check_read_rate(request)
    try:
        workspace = await workspace_repo.get_workspace(db, session_id, version_id=version_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="decision session not found") from exc
    return _workspace_response(workspace)


@router.post(
    "/{session_id}/messages",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session_message(
    session_id: UUID,
    body: CreateDecisionMessageRequest,
    db: SessionDep,
) -> WorkspaceResponse:
    try:
        workspace = await workspace_repo.append_user_message(db, session_id, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="decision session not found") from exc
    return _workspace_response(workspace)


@router.patch("/{session_id}/draft", response_model=WorkspaceResponse)
async def update_session_draft(
    session_id: UUID,
    body: Phase2DraftRequest,
    db: SessionDep,
) -> WorkspaceResponse:
    try:
        workspace = await workspace_repo.update_phase2_draft(
            db,
            session_id,
            body.model_dump(mode="json"),
        )
    except workspace_repo.Phase2DraftConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="decision session not found") from exc
    return _workspace_response(workspace)


@router.post("/{session_id}/runs", response_model=StartAgentRunResponse, status_code=202)
async def start_agent_run(
    session_id: UUID,
    request: Request,
    db: SessionDep,
) -> StartAgentRunResponse:
    session = await session_repo.get_decision_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="decision session not found")

    redis = await _open_redis()
    policy = _demo_policy(redis)
    receipt = None
    try:
        if _demo_safety_is_enabled():
            receipt = await policy.consume_live_run(
                client_ip_from_request(request, get_settings())
            )
        run = await session_repo.create_agent_run(db, session_id)
        job = await redis.enqueue_job(
            "run_phase1_agent_job", str(run.id), _job_id=f"phase1:{run.id}"
        )
        run.job_id = job.job_id if job is not None else f"phase1:{run.id}"
    except Exception:
        if receipt is not None:
            await policy.rollback_live_run(receipt)
        raise
    finally:
        await redis.close()
    await db.commit()
    await db.refresh(run)
    await db.refresh(session)
    return StartAgentRunResponse(
        session=DecisionSessionResponse.model_validate(session),
        run=AgentRunResponse.model_validate(run),
    )


@router.post("/{session_id}/versions", response_model=StartAgentRunResponse, status_code=202)
async def create_phase2_version(
    session_id: UUID,
    request: Request,
    db: SessionDep,
) -> StartAgentRunResponse:
    try:
        await session_repo.validate_phase2_version_request(db, session_id)
    except session_repo.Phase2VersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="decision session not found") from exc

    redis = await _open_redis()
    policy = _demo_policy(redis)
    receipt = None
    run = None
    try:
        if _demo_safety_is_enabled():
            receipt = await policy.consume_live_run(
                client_ip_from_request(request, get_settings())
            )
        run, _version = await session_repo.create_phase2_version_run(db, session_id)
        job = await redis.enqueue_job(
            "run_phase2_agent_job", str(run.id), _job_id=f"phase2:{run.id}"
        )
        run.job_id = job.job_id if job is not None else f"phase2:{run.id}"
    except session_repo.Phase2VersionConflictError as exc:
        if receipt is not None:
            await policy.rollback_live_run(receipt)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        if receipt is not None:
            await policy.rollback_live_run(receipt)
        raise HTTPException(status_code=404, detail="decision session not found") from exc
    except Exception as exc:
        if receipt is not None:
            await policy.rollback_live_run(receipt)
        if run is None:
            raise
        await session_repo.mark_phase2_version_enqueue_failed(db, run.id, exc)
        raise HTTPException(status_code=503, detail="failed to enqueue phase2 run") from exc
    finally:
        await redis.close()
    await db.commit()
    session = await session_repo.get_decision_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="decision session not found")
    await db.refresh(run)
    await db.refresh(session)
    return StartAgentRunResponse(
        session=DecisionSessionResponse.model_validate(session),
        run=AgentRunResponse.model_validate(run),
    )


def _workspace_response(workspace: workspace_repo.Workspace) -> WorkspaceResponse:
    return WorkspaceResponse(
        session=DecisionSessionResponse.model_validate(workspace.session),
        messages=[
            DecisionMessageResponse.model_validate(message) for message in workspace.messages
        ],
        versions=[
            DecisionVersionResponse.model_validate(version) for version in workspace.versions
        ],
        active_version=(
            DecisionVersionResponse.model_validate(workspace.active_version)
            if workspace.active_version is not None
            else None
        ),
        draft=Phase2DraftResponse.model_validate(workspace.draft),
        gap_analysis=GapAnalysisResponse.model_validate(workspace.gap_analysis),
        candidates=[
            DecisionCandidateResponse.model_validate(candidate)
            for candidate in workspace.candidates
        ],
        criteria=[
            DecisionCriterionResponse.model_validate(criterion) for criterion in workspace.criteria
        ],
        evidence_items=[
            EvidenceItemResponse.model_validate(evidence_item)
            for evidence_item in workspace.evidence_items
        ],
        tool_calls=[
            ToolCallResponse.model_validate(tool_call) for tool_call in workspace.tool_calls
        ],
        claims=[ClaimResponse.model_validate(claim) for claim in workspace.claims],
        risk_signals=[
            RiskSignalResponse.model_validate(risk_signal)
            for risk_signal in workspace.risk_signals
        ],
        verification_tasks=[
            VerificationTaskResponse.model_validate(verification_task)
            for verification_task in workspace.verification_tasks
        ],
        score_cells=[
            ScoreCellResponse.model_validate(score_cell) for score_cell in workspace.score_cells
        ],
        recommendations=[
            RecommendationResponse.model_validate(recommendation)
            for recommendation in workspace.recommendations
        ],
        events=[AgentEventResponse.model_validate(event) for event in workspace.events],
    )
