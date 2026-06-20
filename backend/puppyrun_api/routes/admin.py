from typing import Annotated

from arq import create_pool
from fastapi import APIRouter, Header, HTTPException, Request, status

from puppyrun_api.config import get_settings
from puppyrun_api.demo_limits import (
    DemoSafetyPolicy,
    RedisDemoLimitStore,
    client_ip_from_request,
)
from puppyrun_api.schemas import DemoSafetyErrorResponse, DemoSafetyStatusResponse
from puppyrun_worker.main import redis_settings_from_url

router = APIRouter(prefix="/api/v1/admin/demo", tags=["admin"])
AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]


def _require_admin_token(authorization: AuthorizationHeader) -> None:
    settings = get_settings()
    if not settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="admin API is not configured",
        )
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=DemoSafetyErrorResponse(
                code="admin_token_required",
                message="Admin token is required.",
            ).model_dump(mode="json"),
        )
    expected = f"Bearer {settings.admin_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=DemoSafetyErrorResponse(
                code="admin_token_invalid",
                message="Admin token is invalid.",
            ).model_dump(mode="json"),
        )


async def _policy():
    redis = await create_pool(redis_settings_from_url(get_settings().redis_url))
    return redis, DemoSafetyPolicy(settings=get_settings(), store=RedisDemoLimitStore(redis))


@router.get("/status", response_model=DemoSafetyStatusResponse)
async def get_demo_status(request: Request, authorization: AuthorizationHeader = None):
    _require_admin_token(authorization)
    redis, policy = await _policy()
    try:
        return await policy.status_for_ip(client_ip_from_request(request, get_settings()))
    finally:
        await redis.close()


@router.post("/disable", response_model=DemoSafetyStatusResponse)
async def disable_demo(request: Request, authorization: AuthorizationHeader = None):
    _require_admin_token(authorization)
    redis, policy = await _policy()
    try:
        await policy.set_live_demo_enabled(False)
        return await policy.status_for_ip(client_ip_from_request(request, get_settings()))
    finally:
        await redis.close()


@router.post("/enable", response_model=DemoSafetyStatusResponse)
async def enable_demo(request: Request, authorization: AuthorizationHeader = None):
    _require_admin_token(authorization)
    redis, policy = await _policy()
    try:
        await policy.set_live_demo_enabled(True)
        return await policy.status_for_ip(client_ip_from_request(request, get_settings()))
    finally:
        await redis.close()
