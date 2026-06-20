# PuppyRun Phase 5 Public Demo Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a public live demo safety shell around PuppyRun so the no-login demo can default to live DeepSeek while keeping cost, abuse, and disabled-state behavior controlled.

**Architecture:** Keep the Agent workflow unchanged and add protection at the API/Redis boundary. A focused `puppyrun_api.demo_limits` module owns client identity, quota counters, fixed-window read limits, admin switch state, and structured error payloads; public API routes consume that module before creating sessions or enqueuing live runs. The frontend reads a typed API error contract and adds a minimal admin surface without adding full auth or RBAC.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, arq/Redis, SQLAlchemy, pytest, React, TypeScript, Vite, Vitest, Docker Compose, Caddy.

---

## Starting Context

- Repo path: `/Users/jianghuilai/.codex/worktrees/2079/puppy-run`
- Branch: `codex/phase-5`
- Starting commit: `fe1bd754dd2639d7af1f97de2bffae9e8982f878`
- Approved spec: `docs/superpowers/specs/2026-06-20-puppyrun-phase-5-public-demo-safety-design.md`
- Accepted debt: `AD-001` remains out of scope.
- Phase 4 live eval remains a separate release gate and is not replaced by Phase 5 safety tests.

## Scope Guardrails

- Do not add full login, RBAC, billing, export jobs, private repository access, a metrics dashboard, or durable admin audit records.
- Do not change `backend/puppyrun_agent/clarification.py` or reopen accepted debt `AD-001`.
- Do not commit real public hosts, raw IPs, SSH targets, tokens, credentials, or secrets.
- Keep Redis as the runtime store for demo counters and switch state.
- Keep PostgreSQL schema unchanged unless an implementation bug proves impossible to solve without a migration.

## Planned File Structure

Backend:

- Modify `backend/puppyrun_api/config.py`: Phase 5 settings contract.
- Modify `backend/puppyrun_api/schemas.py`: shared demo-safety response models.
- Create `backend/puppyrun_api/demo_limits.py`: Redis-backed quota/rate-limit/switch logic.
- Create `backend/puppyrun_api/routes/admin.py`: token-protected admin API.
- Modify `backend/puppyrun_api/routes/__init__.py`: export admin router.
- Modify `backend/puppyrun_api/main.py`: include admin router and return top-level demo-safety errors.
- Modify `backend/puppyrun_api/routes/sessions.py`: apply public demo limits to public endpoints.
- Modify `backend/puppyrun_api/repositories/sessions.py`: add a validation-only Phase 2 rerun precheck so conflict paths do not consume live-run quota.
- Create `backend/tests/test_phase5_demo_limits.py`: demo-limit unit tests.
- Create `backend/tests/test_phase5_public_api_limits.py`: protected public API tests.
- Create `backend/tests/test_phase5_admin_api.py`: admin API tests.
- Modify `backend/tests/test_config.py`: settings contract tests.
- Modify `backend/tests/test_sessions.py` only if existing route fixtures need safety disabled explicitly.

Frontend:

- Modify `apps/web/src/types.ts`: demo-safety error/status/admin types.
- Modify `apps/web/src/api.ts`: typed `ApiError`, admin API functions.
- Modify `apps/web/src/App.tsx`: friendly public errors and admin route.
- Modify `apps/web/src/App.css`: admin panel and alert styling.
- Modify `apps/web/src/App.test.tsx`: public error, polling, and admin tests.

Deployment and docs:

- Modify `.env.example`: local defaults keep demo safety disabled.
- Modify `deploy/vps/.env.example`: public VPS demo safety values.
- Modify `deploy/vps/docker-compose.yml`: pass Phase 5 env vars to API and worker where useful.
- Modify `README.md`: Phase 5 safety behavior and acceptance.
- Modify `deploy/vps/README.md`: public demo operations and admin token setup.
- Modify `docs/resume-highlights.md`: add a planned or implemented highlight only after the final verification task succeeds.

---

## Task 1: Configuration Contract

**Files:**
- Modify: `backend/puppyrun_api/config.py`
- Modify: `backend/tests/test_config.py`

- [ ] **Step 1: Add failing settings tests**

Append these tests to `backend/tests/test_config.py`:

```python
def test_phase5_demo_safety_defaults_are_local_safe(monkeypatch) -> None:
    for key in (
        "PUPPYRUN_DEMO_SAFETY_ENABLED",
        "PUPPYRUN_LIVE_DEMO_ENABLED",
        "PUPPYRUN_ADMIN_TOKEN",
        "PUPPYRUN_LIVE_RUN_DAILY_LIMIT",
        "PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP",
        "PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP",
        "PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP",
        "PUPPYRUN_CLIENT_IP_HEADER",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings()

    assert settings.demo_safety_enabled is False
    assert settings.live_demo_enabled is False
    assert settings.admin_token is None
    assert settings.live_run_daily_limit == 20
    assert settings.live_run_daily_limit_per_ip == 3
    assert settings.session_create_daily_limit_per_ip == 10
    assert settings.read_rate_limit_per_minute_per_ip == 120
    assert settings.client_ip_header is None


def test_phase5_demo_safety_reads_public_demo_env(monkeypatch) -> None:
    monkeypatch.setenv("PUPPYRUN_DEMO_SAFETY_ENABLED", "true")
    monkeypatch.setenv("PUPPYRUN_LIVE_DEMO_ENABLED", "true")
    monkeypatch.setenv("PUPPYRUN_ADMIN_TOKEN", "private-admin-token")
    monkeypatch.setenv("PUPPYRUN_LIVE_RUN_DAILY_LIMIT", "21")
    monkeypatch.setenv("PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP", "4")
    monkeypatch.setenv("PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP", "11")
    monkeypatch.setenv("PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP", "121")
    monkeypatch.setenv("PUPPYRUN_CLIENT_IP_HEADER", "X-Forwarded-For")

    settings = Settings()

    assert settings.demo_safety_enabled is True
    assert settings.live_demo_enabled is True
    assert settings.admin_token == "private-admin-token"
    assert settings.live_run_daily_limit == 21
    assert settings.live_run_daily_limit_per_ip == 4
    assert settings.session_create_daily_limit_per_ip == 11
    assert settings.read_rate_limit_per_minute_per_ip == 121
    assert settings.client_ip_header == "X-Forwarded-For"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_config.py -q
```

Expected: FAIL because `Settings` does not expose the Phase 5 fields yet.

- [ ] **Step 3: Add settings fields**

Modify `backend/puppyrun_api/config.py` inside `class Settings`:

```python
    demo_safety_enabled: bool = False
    live_demo_enabled: bool = False
    admin_token: str | None = None
    live_run_daily_limit: int = 20
    live_run_daily_limit_per_ip: int = 3
    session_create_daily_limit_per_ip: int = 10
    read_rate_limit_per_minute_per_ip: int = 120
    client_ip_header: str | None = None
```

Keep these near the other deployment/runtime settings, after `cors_origins` or after provider settings. Do not change provider defaults in this task.

- [ ] **Step 4: Run config tests**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 5: Run formatting safety check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add backend/puppyrun_api/config.py backend/tests/test_config.py
git commit -m "feat: add phase5 demo safety config"
```

---

## Task 2: Redis Demo-Limit Core

**Files:**
- Create: `backend/puppyrun_api/demo_limits.py`
- Modify: `backend/puppyrun_api/schemas.py`
- Test: `backend/tests/test_phase5_demo_limits.py`

- [ ] **Step 1: Add failing unit tests for client identity, counters, reset, and switch**

Create `backend/tests/test_phase5_demo_limits.py`:

```python
from datetime import datetime, timezone

import pytest
from fastapi import Request

from puppyrun_api.config import Settings
from puppyrun_api.demo_limits import (
    DemoLimitExceeded,
    DemoLimitStore,
    DemoSafetyPolicy,
    LiveDemoDisabled,
    client_ip_from_request,
)


class InMemoryStore(DemoLimitStore):
    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}
        self.expirations: dict[str, int] = {}

    async def get_int(self, key: str) -> int:
        return int(self.values.get(key, 0))

    async def increment(self, key: str, ttl_seconds: int) -> int:
        self.values[key] = int(self.values.get(key, 0)) + 1
        self.expirations[key] = ttl_seconds
        return int(self.values[key])

    async def decrement(self, key: str) -> int:
        self.values[key] = max(0, int(self.values.get(key, 0)) - 1)
        return int(self.values[key])

    async def get_text(self, key: str) -> str | None:
        value = self.values.get(key)
        return str(value) if value is not None else None

    async def set_text(self, key: str, value: str) -> None:
        self.values[key] = value


def make_request(headers: list[tuple[bytes, bytes]], host: str = "203.0.113.10") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (host, 12345),
        }
    )


def test_client_ip_uses_direct_client_without_trusted_header() -> None:
    request = make_request([(b"x-forwarded-for", b"198.51.100.1")])
    settings = Settings(client_ip_header=None)

    assert client_ip_from_request(request, settings) == "203.0.113.10"


def test_client_ip_uses_first_forwarded_value_when_configured() -> None:
    request = make_request([(b"x-forwarded-for", b"198.51.100.1, 198.51.100.2")])
    settings = Settings(client_ip_header="X-Forwarded-For")

    assert client_ip_from_request(request, settings) == "198.51.100.1"


@pytest.mark.asyncio
async def test_session_create_quota_blocks_after_daily_ip_limit() -> None:
    store = InMemoryStore()
    settings = Settings(demo_safety_enabled=True, session_create_daily_limit_per_ip=1)
    policy = DemoSafetyPolicy(settings=settings, store=store)
    now = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)

    first = await policy.consume_session_create("198.51.100.8", now=now)

    assert first.remaining == 0
    with pytest.raises(DemoLimitExceeded) as exc_info:
        await policy.consume_session_create("198.51.100.8", now=now)
    assert exc_info.value.payload.code == "session_create_daily_limit_exceeded"
    assert exc_info.value.payload.limit == 1
    assert exc_info.value.payload.remaining == 0


@pytest.mark.asyncio
async def test_live_run_quota_checks_global_and_ip_limits() -> None:
    store = InMemoryStore()
    settings = Settings(
        demo_safety_enabled=True,
        live_demo_enabled=True,
        live_run_daily_limit=2,
        live_run_daily_limit_per_ip=1,
    )
    policy = DemoSafetyPolicy(settings=settings, store=store)
    now = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)

    first = await policy.consume_live_run("198.51.100.9", now=now)

    assert first.global_remaining == 1
    assert first.ip_remaining == 0
    with pytest.raises(DemoLimitExceeded) as exc_info:
        await policy.consume_live_run("198.51.100.9", now=now)
    assert exc_info.value.payload.code == "live_run_ip_daily_limit_exceeded"


@pytest.mark.asyncio
async def test_live_run_blocks_when_switch_disabled() -> None:
    store = InMemoryStore()
    settings = Settings(demo_safety_enabled=True, live_demo_enabled=False)
    policy = DemoSafetyPolicy(settings=settings, store=store)

    with pytest.raises(LiveDemoDisabled) as exc_info:
        await policy.consume_live_run("198.51.100.10")

    assert exc_info.value.payload.code == "live_demo_disabled"


@pytest.mark.asyncio
async def test_admin_switch_overrides_default_enabled_state() -> None:
    store = InMemoryStore()
    settings = Settings(demo_safety_enabled=True, live_demo_enabled=True)
    policy = DemoSafetyPolicy(settings=settings, store=store)

    assert await policy.live_demo_is_enabled() is True

    await policy.set_live_demo_enabled(False)
    assert await policy.live_demo_is_enabled() is False

    await policy.set_live_demo_enabled(True)
    assert await policy.live_demo_is_enabled() is True


@pytest.mark.asyncio
async def test_status_uses_counts_without_exposing_raw_ip() -> None:
    store = InMemoryStore()
    settings = Settings(demo_safety_enabled=True, live_demo_enabled=True)
    policy = DemoSafetyPolicy(settings=settings, store=store)

    await policy.consume_session_create("198.51.100.11")
    await policy.consume_live_run("198.51.100.11")
    status = await policy.status_for_ip("198.51.100.11")

    assert status.live_demo_enabled is True
    assert status.global_live_runs_used == 1
    assert status.caller_live_runs_used == 1
    assert status.caller_session_creates_used == 1
    assert "198.51.100.11" not in status.model_dump_json()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_phase5_demo_limits.py -q
```

Expected: FAIL because `puppyrun_api.demo_limits` and schemas do not exist yet.

- [ ] **Step 3: Add response models**

Append to `backend/puppyrun_api/schemas.py`:

```python
class DemoSafetyErrorResponse(BaseModel):
    code: str
    message: str
    limit: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None


class DemoSafetyStatusResponse(BaseModel):
    demo_safety_enabled: bool
    live_demo_enabled: bool
    global_live_run_daily_limit: int
    global_live_runs_used: int
    global_live_runs_remaining: int
    live_run_daily_limit_per_ip: int
    caller_live_runs_used: int
    caller_live_runs_remaining: int
    session_create_daily_limit_per_ip: int
    caller_session_creates_used: int
    caller_session_creates_remaining: int
    read_rate_limit_per_minute_per_ip: int
    reset_at: datetime
```

- [ ] **Step 4: Add demo-limit implementation**

Create `backend/puppyrun_api/demo_limits.py` with these public names:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
from typing import Protocol

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
    def __init__(self, redis) -> None:
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
    return datetime.now(timezone.utc)


def _next_utc_midnight(now: datetime) -> datetime:
    next_day = now.date() + timedelta(days=1)
    return datetime.combine(next_day, time.min, tzinfo=timezone.utc)


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
            return SessionQuotaReceipt(key=None, remaining=self.settings.session_create_daily_limit_per_ip)
        now = now or _utc_now()
        key = self._ip_day_key("session_create", ip_address, now)
        used = await self.store.increment(key, _seconds_until_reset(now))
        limit = self.settings.session_create_daily_limit_per_ip
        if used > limit:
            await self.store.decrement(key)
            raise DemoLimitExceeded(
                self._payload(
                    "session_create_daily_limit_exceeded",
                    "This public demo has reached today's session limit for your network. Please try again after the reset.",
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
            return LiveRunQuotaReceipt(None, None, self.settings.live_run_daily_limit, self.settings.live_run_daily_limit_per_ip)
        if not await self.live_demo_is_enabled():
            raise LiveDemoDisabled(
                self._payload(
                    "live_demo_disabled",
                    "The public live demo is temporarily disabled. Existing sessions remain available.",
                    None,
                    None,
                    now or _utc_now(),
                )
            )
        now = now or _utc_now()
        global_key = self._global_day_key("live_run", now)
        ip_key = self._ip_day_key("live_run", ip_address, now)
        global_used = await self.store.increment(global_key, _seconds_until_reset(now))
        if global_used > self.settings.live_run_daily_limit:
            await self.store.decrement(global_key)
            raise DemoLimitExceeded(
                self._payload(
                    "live_run_daily_limit_exceeded",
                    "The public live demo has reached today's run limit. Please try again after the reset.",
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
                    "This public demo has reached today's live-run limit for your network. Please try again after the reset.",
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

    async def check_read_rate(self, ip_address: str, *, now: datetime | None = None) -> None:
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
                    message="The public demo is receiving too many requests from your network. Please pause briefly and try again.",
                    limit=limit,
                    remaining=0,
                    reset_at=now.replace(second=0, microsecond=0) + timedelta(minutes=1),
                )
            )

    async def live_demo_is_enabled(self) -> bool:
        value = await self.store.get_text("puppyrun:demo:live_enabled")
        if value is None:
            return self.settings.live_demo_enabled
        return value == "true"

    async def set_live_demo_enabled(self, enabled: bool) -> None:
        await self.store.set_text("puppyrun:demo:live_enabled", "true" if enabled else "false")

    async def status_for_ip(self, ip_address: str, *, now: datetime | None = None) -> DemoSafetyStatusResponse:
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
            global_live_runs_remaining=max(0, self.settings.live_run_daily_limit - global_used),
            live_run_daily_limit_per_ip=self.settings.live_run_daily_limit_per_ip,
            caller_live_runs_used=live_ip_used,
            caller_live_runs_remaining=max(0, self.settings.live_run_daily_limit_per_ip - live_ip_used),
            session_create_daily_limit_per_ip=self.settings.session_create_daily_limit_per_ip,
            caller_session_creates_used=session_ip_used,
            caller_session_creates_remaining=max(0, self.settings.session_create_daily_limit_per_ip - session_ip_used),
            read_rate_limit_per_minute_per_ip=self.settings.read_rate_limit_per_minute_per_ip,
            reset_at=_next_utc_midnight(now),
        )

    def _global_day_key(self, kind: str, now: datetime) -> str:
        return f"puppyrun:demo:{kind}:{now.date().isoformat()}:global"

    def _ip_day_key(self, kind: str, ip_address: str, now: datetime) -> str:
        return f"puppyrun:demo:{kind}:{now.date().isoformat()}:ip:{_ip_fingerprint(ip_address)}"

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
```

Keep lines below 100 columns; split constructor calls when needed.

- [ ] **Step 5: Run unit tests**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_phase5_demo_limits.py -q
```

Expected: PASS.

- [ ] **Step 6: Run related backend checks**

Run:

```bash
cd backend && .venv/bin/ruff check puppyrun_api/demo_limits.py tests/test_phase5_demo_limits.py
git diff --check
```

Expected: both commands pass.

- [ ] **Step 7: Commit**

```bash
git add backend/puppyrun_api/demo_limits.py backend/puppyrun_api/schemas.py backend/tests/test_phase5_demo_limits.py
git commit -m "feat: add redis demo safety policy"
```

---

## Task 3: Public API Protection

**Files:**
- Modify: `backend/puppyrun_api/main.py`
- Modify: `backend/puppyrun_api/routes/sessions.py`
- Modify: `backend/puppyrun_api/repositories/sessions.py`
- Create: `backend/tests/test_phase5_public_api_limits.py`

- [ ] **Step 1: Add failing public API tests**

Create `backend/tests/test_phase5_public_api_limits.py` with test fixtures based on `backend/tests/test_sessions.py`. Use an in-memory SQLite DB, monkeypatch `routes.sessions.create_pool`, and configure settings through `get_settings.cache_clear()` plus environment variables:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_api.config import get_settings
from puppyrun_api.db import Base, get_session
from puppyrun_api.main import create_app


class FakeJob:
    job_id = "phase5:test-job"


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value) -> None:
        self.values[key] = value

    async def incr(self, key: str) -> int:
        self.values[key] = int(self.values.get(key, 0)) + 1
        return int(self.values[key])

    async def decr(self, key: str) -> int:
        self.values[key] = int(self.values.get(key, 0)) - 1
        return int(self.values[key])

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def enqueue_job(self, name: str, run_id: str, _job_id: str):
        return FakeJob()

    async def close(self) -> None:
        return None


@pytest.fixture
async def phase5_client(monkeypatch: pytest.MonkeyPatch):
    fake_redis = FakeRedis()
    monkeypatch.setenv("PUPPYRUN_DEMO_SAFETY_ENABLED", "true")
    monkeypatch.setenv("PUPPYRUN_LIVE_DEMO_ENABLED", "true")
    monkeypatch.setenv("PUPPYRUN_LIVE_RUN_DAILY_LIMIT", "1")
    monkeypatch.setenv("PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP", "1")
    monkeypatch.setenv("PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP", "1")
    monkeypatch.setenv("PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP", "2")
    get_settings.cache_clear()

    async def fake_create_pool(settings):
        return fake_redis

    monkeypatch.setattr("puppyrun_api.routes.sessions.create_pool", fake_create_pool)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, fake_redis

    get_settings.cache_clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_session_create_daily_ip_quota_returns_structured_429(phase5_client) -> None:
    client, _redis = phase5_client

    first = await client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime."},
    )
    second = await client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare CrewAI and AutoGen for a stateful Agent runtime."},
    )

    assert first.status_code == 201
    assert second.status_code == 429
    assert second.json()["code"] == "session_create_daily_limit_exceeded"
    assert second.json()["limit"] == 1
    assert second.json()["remaining"] == 0


@pytest.mark.asyncio
async def test_live_run_quota_blocks_second_run_with_structured_429(phase5_client) -> None:
    client, _redis = phase5_client
    create = await client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime."},
    )
    session_id = create.json()["id"]

    first = await client.post(f"/api/v1/sessions/{session_id}/runs")
    second = await client.post(f"/api/v1/sessions/{session_id}/runs")

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["code"] == "live_run_daily_limit_exceeded"


@pytest.mark.asyncio
async def test_missing_session_run_does_not_consume_live_quota(phase5_client) -> None:
    client, _redis = phase5_client

    missing = await client.post("/api/v1/sessions/00000000-0000-0000-0000-000000000001/runs")
    create = await client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime."},
    )
    allowed = await client.post(f"/api/v1/sessions/{create.json()['id']}/runs")

    assert missing.status_code == 404
    assert allowed.status_code == 202


@pytest.mark.asyncio
async def test_disabled_live_demo_blocks_new_run(phase5_client) -> None:
    client, fake_redis = phase5_client
    fake_redis.values["puppyrun:demo:live_enabled"] = "false"
    create = await client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime."},
    )

    response = await client.post(f"/api/v1/sessions/{create.json()['id']}/runs")

    assert response.status_code == 403
    assert response.json()["code"] == "live_demo_disabled"


@pytest.mark.asyncio
async def test_read_rate_limit_keeps_error_payload_top_level(phase5_client) -> None:
    client, _redis = phase5_client

    first = await client.get("/api/v1/sessions")
    second = await client.get("/api/v1/sessions")
    third = await client.get("/api/v1/sessions")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["code"] == "read_rate_limit_exceeded"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_phase5_public_api_limits.py -q
```

Expected: FAIL because routes do not call demo-safety logic and demo-safety exceptions are not rendered top-level.

- [ ] **Step 3: Add top-level demo-safety exception handling**

Modify `backend/puppyrun_api/main.py`:

```python
from fastapi.responses import JSONResponse

from puppyrun_api.demo_limits import DemoSafetyException
from puppyrun_api.routes import admin, health, sessions
```

Inside `create_app()` before routers:

```python
    @app.exception_handler(DemoSafetyException)
    async def demo_safety_exception_handler(_request, exc: DemoSafetyException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.payload.model_dump(mode="json"),
        )
```

Include admin router in Task 4; for this task only importing `admin` can wait until the admin file exists.

- [ ] **Step 4: Add validation-only Phase 2 precheck**

Modify `backend/puppyrun_api/repositories/sessions.py`:

```python
async def validate_phase2_version_request(db: AsyncSession, session_id: UUID) -> None:
    session = await db.get(DecisionSession, session_id)
    if session is None:
        raise ValueError(f"decision session not found: {session_id}")

    raw_draft = _phase2_draft(session.decision_context)
    draft = normalize_phase2_draft(raw_draft, None)
    if raw_draft is None or not _draft_has_changes(draft):
        raise Phase2VersionConflictError("no phase2 draft changes found")

    source_version = await _completed_source_version(db, session_id, draft.get("source_version_id"))
    if source_version is None:
        raise Phase2VersionConflictError("no completed source version found")
```

Call this precheck from `create_phase2_version_run()` at the beginning or keep duplicated logic minimal by reusing a helper that returns `(session, draft, source_version)`. The important contract is that the public route can validate `404` and `409` before consuming live-run quota.

- [ ] **Step 5: Wire demo safety into sessions routes**

Modify `backend/puppyrun_api/routes/sessions.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Request, status

from puppyrun_api.demo_limits import (
    DemoSafetyPolicy,
    RedisDemoLimitStore,
    client_ip_from_request,
)
```

Add helper functions near `SessionDep`:

```python
def _demo_safety_is_enabled() -> bool:
    return get_settings().demo_safety_enabled


def _demo_policy(redis) -> DemoSafetyPolicy:
    return DemoSafetyPolicy(settings=get_settings(), store=RedisDemoLimitStore(redis))


async def _open_redis():
    return await create_pool(redis_settings_from_url(get_settings().redis_url))
```

For `create_session`, add `request: Request` and wrap creation:

```python
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
        receipt = await policy.consume_session_create(client_ip_from_request(request, get_settings()))
        session = await session_repo.create_decision_session(db, body.prompt)
        return DecisionSessionResponse.model_validate(session)
    except Exception:
        if receipt is not None:
            await policy.rollback_session_create(receipt)
        raise
    finally:
        await redis.close()
```

For `list_sessions` and `get_session_workspace`, add `request: Request` and call:

```python
    if _demo_safety_is_enabled():
        redis = await _open_redis()
        try:
            await _demo_policy(redis).check_read_rate(client_ip_from_request(request, get_settings()))
        finally:
            await redis.close()
```

For `start_agent_run`, check session existence first, then consume live-run quota using the same Redis pool used to enqueue the job. Roll back the receipt if enqueue fails before the `202` response is accepted:

```python
    redis = await _open_redis()
    policy = _demo_policy(redis)
    receipt = None
    try:
        if _demo_safety_is_enabled():
            receipt = await policy.consume_live_run(client_ip_from_request(request, get_settings()))
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
```

For `create_phase2_version`, call `validate_phase2_version_request()` before opening Redis and consuming live-run quota. After quota is consumed, call `create_phase2_version_run()`, enqueue the phase2 job, and roll back quota if enqueue fails before returning `202`.

- [ ] **Step 6: Run public API tests**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_phase5_public_api_limits.py tests/test_sessions.py -q
```

Expected: PASS. If existing `test_sessions.py` fixtures fail because they do not monkeypatch Redis, set `PUPPYRUN_DEMO_SAFETY_ENABLED=false` inside their fixtures or rely on the default false setting.

- [ ] **Step 7: Run related backend checks**

Run:

```bash
cd backend && .venv/bin/ruff check puppyrun_api/routes/sessions.py puppyrun_api/main.py puppyrun_api/repositories/sessions.py tests/test_phase5_public_api_limits.py
git diff --check
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/puppyrun_api/main.py backend/puppyrun_api/routes/sessions.py backend/puppyrun_api/repositories/sessions.py backend/tests/test_phase5_public_api_limits.py backend/tests/test_sessions.py
git commit -m "feat: protect public demo api with quotas"
```

---

## Task 4: Admin API

**Files:**
- Create: `backend/puppyrun_api/routes/admin.py`
- Modify: `backend/puppyrun_api/routes/__init__.py`
- Modify: `backend/puppyrun_api/main.py`
- Create: `backend/tests/test_phase5_admin_api.py`

- [ ] **Step 1: Add failing admin API tests**

Create `backend/tests/test_phase5_admin_api.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from puppyrun_api.config import get_settings
from puppyrun_api.main import create_app


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int | str] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value) -> None:
        self.values[key] = value

    async def incr(self, key: str) -> int:
        self.values[key] = int(self.values.get(key, 0)) + 1
        return int(self.values[key])

    async def decr(self, key: str) -> int:
        self.values[key] = max(0, int(self.values.get(key, 0)) - 1)
        return int(self.values[key])

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
async def admin_client(monkeypatch: pytest.MonkeyPatch):
    fake_redis = FakeRedis()
    monkeypatch.setenv("PUPPYRUN_DEMO_SAFETY_ENABLED", "true")
    monkeypatch.setenv("PUPPYRUN_LIVE_DEMO_ENABLED", "true")
    monkeypatch.setenv("PUPPYRUN_ADMIN_TOKEN", "private-admin-token")
    get_settings.cache_clear()

    async def fake_create_pool(settings):
        return fake_redis

    monkeypatch.setattr("puppyrun_api.routes.admin.create_pool", fake_create_pool)

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, fake_redis

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_admin_status_rejects_missing_and_invalid_token(admin_client) -> None:
    client, _redis = admin_client

    missing = await client.get("/api/v1/admin/demo/status")
    invalid = await client.get(
        "/api/v1/admin/demo/status",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert missing.status_code == 401
    assert missing.json()["code"] == "admin_token_required"
    assert invalid.status_code == 403
    assert invalid.json()["code"] == "admin_token_invalid"


@pytest.mark.asyncio
async def test_admin_status_returns_limits_without_secret(admin_client) -> None:
    client, _redis = admin_client

    response = await client.get(
        "/api/v1/admin/demo/status",
        headers={"Authorization": "Bearer private-admin-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["demo_safety_enabled"] is True
    assert payload["live_demo_enabled"] is True
    assert payload["global_live_run_daily_limit"] == 20
    assert "private-admin-token" not in response.text


@pytest.mark.asyncio
async def test_admin_disable_and_enable_live_demo(admin_client) -> None:
    client, _redis = admin_client
    headers = {"Authorization": "Bearer private-admin-token"}

    disabled = await client.post("/api/v1/admin/demo/disable", headers=headers)
    status_disabled = await client.get("/api/v1/admin/demo/status", headers=headers)
    enabled = await client.post("/api/v1/admin/demo/enable", headers=headers)
    status_enabled = await client.get("/api/v1/admin/demo/status", headers=headers)

    assert disabled.status_code == 200
    assert status_disabled.json()["live_demo_enabled"] is False
    assert enabled.status_code == 200
    assert status_enabled.json()["live_demo_enabled"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_phase5_admin_api.py -q
```

Expected: FAIL because admin routes do not exist.

- [ ] **Step 3: Add admin router**

Create `backend/puppyrun_api/routes/admin.py`:

```python
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
```

- [ ] **Step 4: Flatten structured HTTPException details in `main.py`**

If admin tests receive nested detail such as `{"detail": {"code": "admin_token_required", "message": "Admin token is required."}}`, add a small exception handler in `backend/puppyrun_api/main.py`:

```python
from fastapi import HTTPException

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
```

Keep the existing 404 session errors as `{"detail": "decision session not found"}`.

- [ ] **Step 5: Export and include admin router**

Modify `backend/puppyrun_api/routes/__init__.py`:

```python
from puppyrun_api.routes import admin, health, sessions

__all__ = ["admin", "health", "sessions"]
```

Modify `backend/puppyrun_api/main.py`:

```python
from puppyrun_api.routes import admin, health, sessions
```

and:

```python
    app.include_router(admin.router)
```

- [ ] **Step 6: Run admin tests**

Run:

```bash
cd backend && .venv/bin/pytest tests/test_phase5_admin_api.py tests/test_phase5_public_api_limits.py -q
```

Expected: PASS.

- [ ] **Step 7: Run related backend checks**

Run:

```bash
cd backend && .venv/bin/ruff check puppyrun_api/routes/admin.py puppyrun_api/main.py tests/test_phase5_admin_api.py
git diff --check
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/puppyrun_api/routes/admin.py backend/puppyrun_api/routes/__init__.py backend/puppyrun_api/main.py backend/tests/test_phase5_admin_api.py
git commit -m "feat: add phase5 demo admin api"
```

---

## Task 5: Frontend Public Error Handling

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/App.test.tsx`

- [ ] **Step 1: Add failing API error tests**

In `apps/web/src/App.test.tsx`, add to the existing `describe("api functions", () => {` block:

```typescript
  it("throws typed API errors for demo safety responses", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          code: "live_run_daily_limit_exceeded",
          message: "The public live demo has reached today's run limit.",
          limit: 20,
          remaining: 0,
          reset_at: "2026-06-21T00:00:00Z"
        }),
        {
          status: 429,
          headers: { "Content-Type": "application/json" }
        }
      )
    );
    vi.stubGlobal("fetch", fetchMock);
    const actualApi = await vi.importActual<typeof import("./api")>("./api");

    await expect(actualApi.startRun("session-1")).rejects.toMatchObject({
      status: 429,
      code: "live_run_daily_limit_exceeded",
      message: "The public live demo has reached today's run limit."
    });
  });
```

Add an App-level test:

```typescript
  it("shows friendly public demo quota errors", async () => {
    const ready = makeSession("created");
    const workspace = makeWorkspace(
      Object.assign({}, ready, { workflow_stage: "ready_for_research" })
    );

    listSessionsMock.mockImplementation(async () => [workspace.session]);
    getWorkspaceMock.mockImplementation(async () => workspace);
    startRunMock.mockRejectedValue({
      status: 429,
      code: "live_run_daily_limit_exceeded",
      message: "The public live demo has reached today's run limit. Please try again after the reset."
    });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(getRunButton().disabled).toBe(false);
    });
    fireEvent.click(getRunButton());

    await waitFor(() => {
      expect(screen.getByText(/public live demo has reached today's run limit/i)).toBeTruthy();
    });
  });
```

- [ ] **Step 2: Run frontend tests to verify they fail**

Run:

```bash
cd apps/web && npm test -- --run App.test.tsx
```

Expected: FAIL because `api.ts` throws plain `Error` and `App.tsx` renders `String(err)`.

- [ ] **Step 3: Add frontend types**

Append to `apps/web/src/types.ts`:

```typescript
export interface DemoSafetyError {
  code: string;
  message: string;
  limit: number | null;
  remaining: number | null;
  reset_at: string | null;
}

export interface DemoSafetyStatus {
  demo_safety_enabled: boolean;
  live_demo_enabled: boolean;
  global_live_run_daily_limit: number;
  global_live_runs_used: number;
  global_live_runs_remaining: number;
  live_run_daily_limit_per_ip: number;
  caller_live_runs_used: number;
  caller_live_runs_remaining: number;
  session_create_daily_limit_per_ip: number;
  caller_session_creates_used: number;
  caller_session_creates_remaining: number;
  read_rate_limit_per_minute_per_ip: number;
  reset_at: string;
}
```

- [ ] **Step 4: Add typed API errors**

Modify `apps/web/src/api.ts`:

```typescript
import type {
  DecisionSession,
  DemoSafetyError,
  DemoSafetyStatus,
  Phase2Draft,
  StartAgentRunResponse,
  Workspace
} from "./types";

export class ApiError extends Error {
  status: number;
  code: string | null;
  limit: number | null;
  remaining: number | null;
  reset_at: string | null;

  constructor(status: number, payload: Partial<DemoSafetyError>) {
    super(payload.message ?? `Request failed: ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = payload.code ?? null;
    this.limit = payload.limit ?? null;
    this.remaining = payload.remaining ?? null;
    this.reset_at = payload.reset_at ?? null;
  }
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  try {
    const payload = (await response.json()) as Partial<DemoSafetyError>;
    return new ApiError(response.status, payload);
  } catch {
    return new ApiError(response.status, { message: `Request failed: ${response.status}` });
  }
}
```

Change the failure branch in `request<T>`:

```typescript
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
```

Add admin calls now so Task 6 can use them:

```typescript
export async function getDemoStatus(adminToken: string): Promise<DemoSafetyStatus> {
  return request<DemoSafetyStatus>("/api/v1/admin/demo/status", {
    headers: { Authorization: `Bearer ${adminToken}` }
  });
}

export async function setDemoLiveEnabled(
  adminToken: string,
  enabled: boolean
): Promise<DemoSafetyStatus> {
  return request<DemoSafetyStatus>(`/api/v1/admin/demo/${enabled ? "enable" : "disable"}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${adminToken}` }
  });
}
```

- [ ] **Step 5: Render friendly messages in App**

Modify `apps/web/src/App.tsx`:

```typescript
import {
  ApiError,
  createDecisionVersion,
  createSession,
  getWorkspace,
  listSessions,
  sendMessage,
  startRun,
  updateDraft
} from "./api";
```

Add helper near other helper functions:

```typescript
function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return String(err);
}
```

Replace `setError(String(err))` with `setError(errorMessage(err))` across public handlers. Keep polling interval failures silent as today.

- [ ] **Step 6: Run frontend tests**

Run:

```bash
cd apps/web && npm test -- --run App.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Run frontend lint/build surface**

Run:

```bash
cd apps/web && npm run build
git diff --check
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/types.ts apps/web/src/api.ts apps/web/src/App.tsx apps/web/src/App.test.tsx
git commit -m "feat: show public demo safety errors"
```

---

## Task 6: Minimal Admin UI

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/App.css`
- Modify: `apps/web/src/App.test.tsx`

- [ ] **Step 1: Add failing admin UI tests**

In `apps/web/src/App.test.tsx`, extend the mocked API import and `vi.mock` block to include:

```typescript
  getDemoStatus,
  setDemoLiveEnabled,
```

and:

```typescript
  getDemoStatus: vi.fn(),
  setDemoLiveEnabled: vi.fn(),
```

Add mocks:

```typescript
const getDemoStatusMock = vi.mocked(getDemoStatus);
const setDemoLiveEnabledMock = vi.mocked(setDemoLiveEnabled);
```

Reset them in `beforeEach`.

Add tests:

```typescript
  it("shows admin status and toggles live demo on the admin route", async () => {
    window.history.pushState({}, "", "/admin");
    getDemoStatusMock.mockResolvedValue({
      demo_safety_enabled: true,
      live_demo_enabled: true,
      global_live_run_daily_limit: 20,
      global_live_runs_used: 4,
      global_live_runs_remaining: 16,
      live_run_daily_limit_per_ip: 3,
      caller_live_runs_used: 1,
      caller_live_runs_remaining: 2,
      session_create_daily_limit_per_ip: 10,
      caller_session_creates_used: 2,
      caller_session_creates_remaining: 8,
      read_rate_limit_per_minute_per_ip: 120,
      reset_at: "2026-06-21T00:00:00Z"
    });
    setDemoLiveEnabledMock.mockResolvedValue({
      demo_safety_enabled: true,
      live_demo_enabled: false,
      global_live_run_daily_limit: 20,
      global_live_runs_used: 4,
      global_live_runs_remaining: 16,
      live_run_daily_limit_per_ip: 3,
      caller_live_runs_used: 1,
      caller_live_runs_remaining: 2,
      session_create_daily_limit_per_ip: 10,
      caller_session_creates_used: 2,
      caller_session_creates_remaining: 8,
      read_rate_limit_per_minute_per_ip: 120,
      reset_at: "2026-06-21T00:00:00Z"
    });

    render(<App />);
    fireEvent.change(screen.getByLabelText("Admin token"), {
      target: { value: "private-admin-token" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Load admin status" }));

    await waitFor(() => {
      expect(screen.getByText("Live demo enabled")).toBeTruthy();
      expect(screen.getByText("4 / 20")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Disable live demo" }));
    await waitFor(() => {
      expect(setDemoLiveEnabledMock).toHaveBeenCalledWith("private-admin-token", false);
      expect(screen.getByText("Live demo disabled")).toBeTruthy();
    });
  });

  it("keeps admin token out of public workbench", async () => {
    window.history.pushState({}, "", "/");
    listSessionsMock.mockResolvedValue([]);

    render(<App />);
    await flushAsyncUpdates();

    expect(screen.queryByLabelText("Admin token")).toBeNull();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd apps/web && npm test -- --run App.test.tsx
```

Expected: FAIL because `/admin` is not implemented.

- [ ] **Step 3: Add admin view state and render branch**

Modify imports in `apps/web/src/App.tsx`:

```typescript
import {
  ApiError,
  createDecisionVersion,
  createSession,
  getDemoStatus,
  getWorkspace,
  listSessions,
  sendMessage,
  setDemoLiveEnabled,
  startRun,
  updateDraft
} from "./api";
import type {
  CandidateOverrideAction,
  DecisionCandidate,
  DecisionCriterion,
  DecisionSession,
  DemoSafetyStatus,
  Phase2Draft,
  Workspace
} from "./types";
```

Add state inside `App()`:

```typescript
  const [adminToken, setAdminToken] = useState(() => window.localStorage.getItem("puppyrun-admin-token") ?? "");
  const [adminStatus, setAdminStatus] = useState<DemoSafetyStatus | null>(null);
  const [adminError, setAdminError] = useState<string | null>(null);
  const [isAdminBusy, setIsAdminBusy] = useState(false);
  const isAdminRoute = window.location.pathname === "/admin";
```

Add admin functions:

```typescript
  async function loadAdminStatus() {
    setIsAdminBusy(true);
    setAdminError(null);
    try {
      window.localStorage.setItem("puppyrun-admin-token", adminToken);
      setAdminStatus(await getDemoStatus(adminToken));
    } catch (err) {
      setAdminError(errorMessage(err));
    } finally {
      setIsAdminBusy(false);
    }
  }

  async function handleAdminToggle(enabled: boolean) {
    setIsAdminBusy(true);
    setAdminError(null);
    try {
      window.localStorage.setItem("puppyrun-admin-token", adminToken);
      setAdminStatus(await setDemoLiveEnabled(adminToken, enabled));
    } catch (err) {
      setAdminError(errorMessage(err));
    } finally {
      setIsAdminBusy(false);
    }
  }
```

Before the public workbench return, add:

```tsx
  if (isAdminRoute) {
    return (
      <main className="app-shell admin-shell">
        <section className="admin-panel" aria-label="Demo admin">
          <p className="eyebrow">PuppyRun Phase 5</p>
          <h1>Public demo controls</h1>
          <label htmlFor="admin-token">Admin token</label>
          <input
            id="admin-token"
            type="password"
            value={adminToken}
            onChange={(event) => setAdminToken(event.target.value)}
          />
          <div className="admin-actions">
            <button disabled={isAdminBusy || adminToken.trim().length === 0} onClick={loadAdminStatus} type="button">
              Load admin status
            </button>
            <button disabled={isAdminBusy || !adminStatus} onClick={() => handleAdminToggle(false)} type="button">
              Disable live demo
            </button>
            <button disabled={isAdminBusy || !adminStatus} onClick={() => handleAdminToggle(true)} type="button">
              Enable live demo
            </button>
          </div>
          {adminError && <p className="error">{adminError}</p>}
          {adminStatus && (
            <section className="admin-status" aria-label="Demo safety status">
              <h2>{adminStatus.live_demo_enabled ? "Live demo enabled" : "Live demo disabled"}</h2>
              <dl>
                <div>
                  <dt>Global live runs</dt>
                  <dd>{adminStatus.global_live_runs_used} / {adminStatus.global_live_run_daily_limit}</dd>
                </div>
                <div>
                  <dt>Your live runs</dt>
                  <dd>{adminStatus.caller_live_runs_used} / {adminStatus.live_run_daily_limit_per_ip}</dd>
                </div>
                <div>
                  <dt>Your sessions</dt>
                  <dd>{adminStatus.caller_session_creates_used} / {adminStatus.session_create_daily_limit_per_ip}</dd>
                </div>
                <div>
                  <dt>Read limit</dt>
                  <dd>{adminStatus.read_rate_limit_per_minute_per_ip} / minute</dd>
                </div>
                <div>
                  <dt>Reset</dt>
                  <dd>{new Date(adminStatus.reset_at).toLocaleString()}</dd>
                </div>
              </dl>
            </section>
          )}
        </section>
      </main>
    );
  }
```

- [ ] **Step 4: Add admin CSS**

Append to `apps/web/src/App.css`:

```css
.admin-shell {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 48px 20px;
}

.admin-panel {
  width: min(720px, 100%);
  background: #ffffff;
  border: 1px solid #d7dde5;
  border-radius: 8px;
  box-sizing: border-box;
  padding: 24px;
}

.admin-panel h1 {
  font-size: 24px;
  line-height: 1.2;
  margin: 4px 0 20px;
}

.admin-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.admin-status {
  border-top: 1px solid #e4e8ee;
  margin-top: 20px;
  padding-top: 20px;
}

.admin-status dl {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.admin-status dt {
  color: #526071;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}

.admin-status dd {
  margin: 4px 0 0;
  font-size: 18px;
  font-weight: 800;
}
```

- [ ] **Step 5: Run frontend tests and build**

Run:

```bash
cd apps/web && npm test -- --run App.test.tsx
cd apps/web && npm run build
git diff --check
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/App.tsx apps/web/src/App.css apps/web/src/App.test.tsx
git commit -m "feat: add minimal demo admin ui"
```

---

## Task 7: Deployment, Docs, And Release Verification

**Files:**
- Modify: `.env.example`
- Modify: `deploy/vps/.env.example`
- Modify: `deploy/vps/docker-compose.yml`
- Modify: `README.md`
- Modify: `deploy/vps/README.md`
- Modify: `docs/resume-highlights.md`

- [ ] **Step 1: Update local env example**

Append to `.env.example`:

```env
# Phase 5 public demo safety settings.
# Local development keeps these disabled unless explicitly testing the public demo gate.
PUPPYRUN_DEMO_SAFETY_ENABLED=false
PUPPYRUN_LIVE_DEMO_ENABLED=false
PUPPYRUN_ADMIN_TOKEN=
PUPPYRUN_LIVE_RUN_DAILY_LIMIT=20
PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP=3
PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP=10
PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP=120
PUPPYRUN_CLIENT_IP_HEADER=
```

- [ ] **Step 2: Update VPS env example**

Append to `deploy/vps/.env.example`:

```env
# Phase 5 public demo safety settings.
PUPPYRUN_DEMO_SAFETY_ENABLED=true
PUPPYRUN_LIVE_DEMO_ENABLED=true
PUPPYRUN_ADMIN_TOKEN=replace-with-private-admin-token
PUPPYRUN_LIVE_RUN_DAILY_LIMIT=20
PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP=3
PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP=10
PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP=120
PUPPYRUN_CLIENT_IP_HEADER=X-Forwarded-For
```

- [ ] **Step 3: Pass env vars through VPS Compose**

Modify `deploy/vps/docker-compose.yml` under both `api.environment` and `worker.environment`:

```yaml
      PUPPYRUN_DEMO_SAFETY_ENABLED: ${PUPPYRUN_DEMO_SAFETY_ENABLED:-true}
      PUPPYRUN_LIVE_DEMO_ENABLED: ${PUPPYRUN_LIVE_DEMO_ENABLED:-true}
      PUPPYRUN_ADMIN_TOKEN: ${PUPPYRUN_ADMIN_TOKEN:-}
      PUPPYRUN_LIVE_RUN_DAILY_LIMIT: ${PUPPYRUN_LIVE_RUN_DAILY_LIMIT:-20}
      PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP: ${PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP:-3}
      PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP: ${PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP:-10}
      PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP: ${PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP:-120}
      PUPPYRUN_CLIENT_IP_HEADER: ${PUPPYRUN_CLIENT_IP_HEADER:-X-Forwarded-For}
```

The worker does not enforce API limits, but passing the same settings keeps runtime inspection consistent.

- [ ] **Step 4: Update README**

Add a `## Phase 5 Public Demo Safety` section after the Phase 4 live eval gate section:

```markdown
## Phase 5 Public Demo Safety

Phase 5 v1 protects the no-login public demo when live DeepSeek is enabled. The API uses Redis-backed counters for a global daily live-run quota, a per-IP daily live-run quota, a per-IP daily session-create quota, and a fixed-minute read limit for polling-heavy endpoints.

Default public-demo limits:

- global live runs per day: `20`
- live runs per IP per day: `3`
- sessions created per IP per day: `10`
- read requests per IP per minute: `120`

The owner can use the token-protected `/admin` UI or `/api/v1/admin/demo/*` API to check current counts and disable or re-enable new live runs. Keep `PUPPYRUN_ADMIN_TOKEN` in private environment files only.
```

Add a short acceptance list:

```markdown
Phase 5 local verification:

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
cd apps/web && npm test -- --run
cd apps/web && npm run build
git diff --check
```
```

- [ ] **Step 5: Update VPS runbook**

In `deploy/vps/README.md`, add a subsection under environment setup:

```markdown
### Phase 5 public demo safety

For a public live DeepSeek demo, set these values in `deploy/vps/.env`:

```env
PUPPYRUN_DEMO_SAFETY_ENABLED=true
PUPPYRUN_LIVE_DEMO_ENABLED=true
PUPPYRUN_ADMIN_TOKEN=replace-with-private-admin-token
PUPPYRUN_LIVE_RUN_DAILY_LIMIT=20
PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP=3
PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP=10
PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP=120
PUPPYRUN_CLIENT_IP_HEADER=X-Forwarded-For
```

Do not commit the real admin token. After deployment, open `/admin`, enter the token, confirm the current counts, disable live demo, verify new runs are blocked, then re-enable it.
```

- [ ] **Step 6: Update resume highlights after verification succeeds**

After all checks in this task pass, append a concise entry to `docs/resume-highlights.md`:

```markdown
### Phase 5 public live demo safety shell

- **What shipped:** Added Redis-backed public demo quotas, live-run kill switch, token-protected admin controls, frontend quota messaging, and VPS configuration for a no-login live DeepSeek demo.
- **Why it matters:** Turns PuppyRun from a local/live-eval prototype into a safer public demo by bounding cost, limiting abuse, and giving the operator a runtime shutoff without full RBAC.
- **Evidence:** Phase 5 backend and frontend tests, Docker Compose config check, and manual browser/admin acceptance from the Phase 5 release session.
- **Status:** Implemented and verified in Phase 5.
```

Do not add this entry until implementation and verification actually pass.

- [ ] **Step 7: Run full verification**

Run:

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
cd apps/web && npm test -- --run
cd apps/web && npm run build
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml config
git diff --check
```

Expected: all commands pass. If `deploy/vps/.env` is absent locally, create it from `deploy/vps/.env.example` and use local non-secret example values only. Do not commit `deploy/vps/.env`.

- [ ] **Step 8: Docker smoke**

Run:

```bash
docker compose up --build -d
curl http://localhost:8000/health
docker compose ps
```

Expected: health returns `{"status":"ok","service":"puppyrun-api"}` and containers are running.

- [ ] **Step 9: Manual browser acceptance**

Start the app locally with Phase 5 safety enabled and low test limits in private local env values. Verify:

1. Public workbench opens at `http://localhost:5173`.
2. A user under quota can create a session and start a run.
3. Exceeding the per-IP live-run limit shows the friendly public message.
4. Exceeding the session-create limit shows the friendly public message.
5. `/admin` loads with the private admin token.
6. Admin status shows counts and limits.
7. Admin disable blocks new live runs.
8. Existing sessions remain readable while disabled.
9. Admin enable allows new runs again when quota permits.
10. Polling or read-limit errors do not erase dirty weight edits.

- [ ] **Step 10: Commit**

```bash
git add .env.example deploy/vps/.env.example deploy/vps/docker-compose.yml README.md deploy/vps/README.md docs/resume-highlights.md
git commit -m "docs: document phase5 public demo safety"
```

---

## Final Phase 5 Gate

Before claiming Phase 5 v1 is complete, run:

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
cd apps/web && npm test -- --run
cd apps/web && npm run build
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml config
git diff --check
```

Then complete the browser/admin acceptance from Task 7 Step 9. If a real public VPS release is part of the current scope, also run the VPS deployment update path and test the public `/health`, workbench, and `/admin` flows. Keep all real host, token, and SSH values outside committed files.

Phase 4 live eval remains separate:

```bash
cd backend
PUPPYRUN_LLM_PROVIDER=deepseek \
PUPPYRUN_DEEPSEEK_API_KEY=replace-with-private-key \
.venv/bin/python -m puppyrun_eval run --suite phase4-live
```

Do not claim Phase 4 live eval passed unless that command runs in the current release session with a real private key.

## Spec Coverage Self-Review

- No-login public demo retained: covered by Tasks 3, 5, 6, and 7.
- Live DeepSeek public path protected by quotas and switch: covered by Tasks 1, 2, 3, 4, and 7.
- Global daily live-run quota: covered by Tasks 2 and 3.
- Per-IP live-run quota: covered by Tasks 2 and 3.
- Per-IP session-create quota: covered by Tasks 2 and 3.
- Read endpoint fixed-window limit: covered by Tasks 2 and 3.
- Admin token, status, enable, disable: covered by Tasks 4 and 6.
- Friendly `403`/`429` frontend messages: covered by Task 5.
- VPS/env/docs: covered by Task 7.
- Accepted debt and auth/RBAC/export/dashboard non-goals: guarded in Scope Guardrails and Task 7.
