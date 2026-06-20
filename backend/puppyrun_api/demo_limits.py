from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
from typing import Any, Protocol

from fastapi import Request, status

from puppyrun_api.config import Settings
from puppyrun_api.schemas import DemoSafetyErrorResponse, DemoSafetyStatusResponse


class DemoLimitStore(Protocol):
    async def get_int(self, key: str) -> int:
        raise NotImplementedError

    async def increment(self, key: str, ttl_seconds: int) -> int:
        raise NotImplementedError

    async def decrement(self, key: str) -> int:
        raise NotImplementedError

    async def get_text(self, key: str) -> str | None:
        raise NotImplementedError

    async def set_text(self, key: str, value: str) -> None:
        raise NotImplementedError


class RedisDemoLimitStore:
    def __init__(self, redis: Any) -> None:
        self.redis = redis

    async def get_int(self, key: str) -> int:
        value = await self.redis.get(key)
        if value is None:
            return 0
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        return int(value)

    async def increment(self, key: str, ttl_seconds: int) -> int:
        value = await self.redis.incr(key)
        if value == 1:
            await self.redis.expire(key, ttl_seconds)
        return int(value)

    async def decrement(self, key: str) -> int:
        value = await self.redis.decr(key)
        if int(value) < 0:
            await self.redis.set(key, 0)
            return 0
        return int(value)

    async def get_text(self, key: str) -> str | None:
        value = await self.redis.get(key)
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    async def set_text(self, key: str, value: str) -> None:
        await self.redis.set(key, value)


@dataclass(frozen=True)
class SessionQuotaReceipt:
    key: str | None
    remaining: int


@dataclass(frozen=True)
class LiveRunQuotaReceipt:
    global_key: str | None
    ip_key: str | None
    global_remaining: int
    ip_remaining: int


class DemoSafetyException(Exception):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS

    def __init__(self, payload: DemoSafetyErrorResponse) -> None:
        super().__init__(payload.message)
        self.payload = payload


class DemoLimitExceeded(DemoSafetyException):
    pass


class LiveDemoDisabled(DemoSafetyException):
    status_code = status.HTTP_403_FORBIDDEN


def client_ip_from_request(request: Request, settings: Settings) -> str:
    if settings.client_ip_header:
        value = request.headers.get(settings.client_ip_header)
        if value:
            first_value = value.split(",", 1)[0].strip()
            if first_value:
                return first_value
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _ip_fingerprint(ip_address: str) -> str:
    return sha256(ip_address.encode("utf-8")).hexdigest()[:16]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _next_utc_midnight(now: datetime) -> datetime:
    next_day = now.date() + timedelta(days=1)
    return datetime.combine(next_day, time.min, tzinfo=UTC)


def _seconds_until_reset(now: datetime) -> int:
    return max(1, int((_next_utc_midnight(now) - now).total_seconds()))


class DemoSafetyPolicy:
    def __init__(self, settings: Settings, store: DemoLimitStore) -> None:
        self.settings = settings
        self.store = store

    async def consume_session_create(
        self,
        ip_address: str,
        *,
        now: datetime | None = None,
    ) -> SessionQuotaReceipt:
        if not self.settings.demo_safety_enabled:
            return SessionQuotaReceipt(
                key=None,
                remaining=self.settings.session_create_daily_limit_per_ip,
            )
        now = now or _utc_now()
        key = self._ip_day_key("session_create", ip_address, now)
        used = await self.store.increment(key, _seconds_until_reset(now))
        limit = self.settings.session_create_daily_limit_per_ip
        if used > limit:
            await self.store.decrement(key)
            raise DemoLimitExceeded(
                self._payload(
                    "session_create_daily_limit_exceeded",
                    (
                        "This public demo has reached today's session limit for "
                        "your network. Please try again after the reset."
                    ),
                    limit,
                    0,
                    now,
                )
            )
        return SessionQuotaReceipt(key=key, remaining=max(0, limit - used))

    async def rollback_session_create(self, receipt: SessionQuotaReceipt) -> None:
        if receipt.key is not None:
            await self.store.decrement(receipt.key)

    async def consume_live_run(
        self,
        ip_address: str,
        *,
        now: datetime | None = None,
    ) -> LiveRunQuotaReceipt:
        if not self.settings.demo_safety_enabled:
            return LiveRunQuotaReceipt(
                None,
                None,
                self.settings.live_run_daily_limit,
                self.settings.live_run_daily_limit_per_ip,
            )
        if not await self.live_demo_is_enabled():
            raise LiveDemoDisabled(
                self._payload(
                    "live_demo_disabled",
                    (
                        "The public live demo is temporarily disabled. Existing "
                        "sessions remain available."
                    ),
                    None,
                    None,
                    now or _utc_now(),
                )
            )
        now = now or _utc_now()
        global_key = self._global_day_key("live_run", now)
        ip_key = self._ip_day_key("live_run", ip_address, now)
        global_used = await self.store.increment(
            global_key,
            _seconds_until_reset(now),
        )
        if global_used > self.settings.live_run_daily_limit:
            await self.store.decrement(global_key)
            raise DemoLimitExceeded(
                self._payload(
                    "live_run_daily_limit_exceeded",
                    (
                        "The public live demo has reached today's run limit. "
                        "Please try again after the reset."
                    ),
                    self.settings.live_run_daily_limit,
                    0,
                    now,
                )
            )
        ip_used = await self.store.increment(ip_key, _seconds_until_reset(now))
        if ip_used > self.settings.live_run_daily_limit_per_ip:
            await self.store.decrement(ip_key)
            await self.store.decrement(global_key)
            raise DemoLimitExceeded(
                self._payload(
                    "live_run_ip_daily_limit_exceeded",
                    (
                        "This public demo has reached today's live-run limit for "
                        "your network. Please try again after the reset."
                    ),
                    self.settings.live_run_daily_limit_per_ip,
                    0,
                    now,
                )
            )
        return LiveRunQuotaReceipt(
            global_key=global_key,
            ip_key=ip_key,
            global_remaining=max(0, self.settings.live_run_daily_limit - global_used),
            ip_remaining=max(0, self.settings.live_run_daily_limit_per_ip - ip_used),
        )

    async def rollback_live_run(self, receipt: LiveRunQuotaReceipt) -> None:
        if receipt.ip_key is not None:
            await self.store.decrement(receipt.ip_key)
        if receipt.global_key is not None:
            await self.store.decrement(receipt.global_key)

    async def check_read_rate(
        self,
        ip_address: str,
        *,
        now: datetime | None = None,
    ) -> None:
        if not self.settings.demo_safety_enabled:
            return
        now = now or _utc_now()
        minute_key = now.strftime("%Y%m%d%H%M")
        key = f"puppyrun:demo:read:{minute_key}:ip:{_ip_fingerprint(ip_address)}"
        used = await self.store.increment(key, 90)
        limit = self.settings.read_rate_limit_per_minute_per_ip
        if used > limit:
            await self.store.decrement(key)
            raise DemoLimitExceeded(
                DemoSafetyErrorResponse(
                    code="read_rate_limit_exceeded",
                    message=(
                        "The public demo is receiving too many requests from "
                        "your network. Please pause briefly and try again."
                    ),
                    limit=limit,
                    remaining=0,
                    reset_at=now.replace(second=0, microsecond=0)
                    + timedelta(minutes=1),
                )
            )

    async def live_demo_is_enabled(self) -> bool:
        value = await self.store.get_text("puppyrun:demo:live_enabled")
        if value is None:
            return self.settings.live_demo_enabled
        return value == "true"

    async def set_live_demo_enabled(self, enabled: bool) -> None:
        await self.store.set_text(
            "puppyrun:demo:live_enabled",
            "true" if enabled else "false",
        )

    async def status_for_ip(
        self,
        ip_address: str,
        *,
        now: datetime | None = None,
    ) -> DemoSafetyStatusResponse:
        now = now or _utc_now()
        global_key = self._global_day_key("live_run", now)
        live_ip_key = self._ip_day_key("live_run", ip_address, now)
        session_ip_key = self._ip_day_key("session_create", ip_address, now)
        global_used = await self.store.get_int(global_key)
        live_ip_used = await self.store.get_int(live_ip_key)
        session_ip_used = await self.store.get_int(session_ip_key)
        return DemoSafetyStatusResponse(
            demo_safety_enabled=self.settings.demo_safety_enabled,
            live_demo_enabled=await self.live_demo_is_enabled(),
            global_live_run_daily_limit=self.settings.live_run_daily_limit,
            global_live_runs_used=global_used,
            global_live_runs_remaining=max(
                0,
                self.settings.live_run_daily_limit - global_used,
            ),
            live_run_daily_limit_per_ip=self.settings.live_run_daily_limit_per_ip,
            caller_live_runs_used=live_ip_used,
            caller_live_runs_remaining=max(
                0,
                self.settings.live_run_daily_limit_per_ip - live_ip_used,
            ),
            session_create_daily_limit_per_ip=(
                self.settings.session_create_daily_limit_per_ip
            ),
            caller_session_creates_used=session_ip_used,
            caller_session_creates_remaining=max(
                0,
                self.settings.session_create_daily_limit_per_ip - session_ip_used,
            ),
            read_rate_limit_per_minute_per_ip=(
                self.settings.read_rate_limit_per_minute_per_ip
            ),
            reset_at=_next_utc_midnight(now),
        )

    def _global_day_key(self, kind: str, now: datetime) -> str:
        return f"puppyrun:demo:{kind}:{now.date().isoformat()}:global"

    def _ip_day_key(self, kind: str, ip_address: str, now: datetime) -> str:
        return (
            f"puppyrun:demo:{kind}:{now.date().isoformat()}:ip:"
            f"{_ip_fingerprint(ip_address)}"
        )

    def _payload(
        self,
        code: str,
        message: str,
        limit: int | None,
        remaining: int | None,
        now: datetime,
    ) -> DemoSafetyErrorResponse:
        return DemoSafetyErrorResponse(
            code=code,
            message=message,
            limit=limit,
            remaining=remaining,
            reset_at=_next_utc_midnight(now),
        )
