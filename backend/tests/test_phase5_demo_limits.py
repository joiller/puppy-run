from datetime import UTC, datetime

import pytest
from fastapi import Request

from puppyrun_api.config import Settings
from puppyrun_api.demo_limits import (
    DemoLimitExceeded,
    DemoLimitStore,
    DemoSafetyPolicy,
    LiveDemoDisabled,
    RedisDemoLimitStore,
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


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes | int | str] = {}
        self.expirations: dict[str, int] = {}

    async def get(self, key: str) -> bytes | int | str | None:
        return self.values.get(key)

    async def incr(self, key: str) -> int:
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = value
        return value

    async def expire(self, key: str, ttl_seconds: int) -> None:
        self.expirations[key] = ttl_seconds

    async def decr(self, key: str) -> int:
        value = int(self.values.get(key, 0)) - 1
        self.values[key] = value
        return value

    async def set(self, key: str, value: int | str) -> None:
        self.values[key] = value


def make_request(
    headers: list[tuple[bytes, bytes]],
    host: str = "203.0.113.10",
) -> Request:
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
    request = make_request(
        [(b"x-forwarded-for", b"198.51.100.1, 198.51.100.2")]
    )
    settings = Settings(client_ip_header="X-Forwarded-For")

    assert client_ip_from_request(request, settings) == "198.51.100.1"


@pytest.mark.asyncio
async def test_session_create_quota_blocks_after_daily_ip_limit() -> None:
    store = InMemoryStore()
    settings = Settings(demo_safety_enabled=True, session_create_daily_limit_per_ip=1)
    policy = DemoSafetyPolicy(settings=settings, store=store)
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)

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
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)

    first = await policy.consume_live_run("198.51.100.9", now=now)

    assert first.global_remaining == 1
    assert first.ip_remaining == 0
    with pytest.raises(DemoLimitExceeded) as exc_info:
        await policy.consume_live_run("198.51.100.9", now=now)
    assert exc_info.value.payload.code == "live_run_ip_daily_limit_exceeded"


@pytest.mark.asyncio
async def test_live_run_quota_blocks_after_global_daily_limit() -> None:
    store = InMemoryStore()
    settings = Settings(
        demo_safety_enabled=True,
        live_demo_enabled=True,
        live_run_daily_limit=1,
        live_run_daily_limit_per_ip=2,
    )
    policy = DemoSafetyPolicy(settings=settings, store=store)
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)

    await policy.consume_live_run("198.51.100.9", now=now)

    with pytest.raises(DemoLimitExceeded) as exc_info:
        await policy.consume_live_run("198.51.100.10", now=now)
    assert exc_info.value.payload.code == "live_run_daily_limit_exceeded"
    assert exc_info.value.payload.limit == 1
    assert exc_info.value.payload.remaining == 0


@pytest.mark.asyncio
async def test_live_run_blocks_when_switch_disabled() -> None:
    store = InMemoryStore()
    settings = Settings(demo_safety_enabled=True, live_demo_enabled=False)
    policy = DemoSafetyPolicy(settings=settings, store=store)

    with pytest.raises(LiveDemoDisabled) as exc_info:
        await policy.consume_live_run("198.51.100.10")

    assert exc_info.value.payload.code == "live_demo_disabled"


@pytest.mark.asyncio
async def test_live_run_disabled_switch_does_not_increment_counters() -> None:
    store = InMemoryStore()
    settings = Settings(demo_safety_enabled=True, live_demo_enabled=False)
    policy = DemoSafetyPolicy(settings=settings, store=store)
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)

    with pytest.raises(LiveDemoDisabled):
        await policy.consume_live_run("198.51.100.10", now=now)

    status = await policy.status_for_ip("198.51.100.10", now=now)
    assert status.global_live_runs_used == 0
    assert status.caller_live_runs_used == 0


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
async def test_read_rate_limit_blocks_with_minute_reset_payload() -> None:
    store = InMemoryStore()
    settings = Settings(demo_safety_enabled=True, read_rate_limit_per_minute_per_ip=2)
    policy = DemoSafetyPolicy(settings=settings, store=store)
    now = datetime(2026, 6, 20, 12, 34, 56, tzinfo=UTC)

    await policy.check_read_rate("198.51.100.12", now=now)
    await policy.check_read_rate("198.51.100.12", now=now)

    with pytest.raises(DemoLimitExceeded) as exc_info:
        await policy.check_read_rate("198.51.100.12", now=now)
    assert exc_info.value.payload.code == "read_rate_limit_exceeded"
    assert exc_info.value.payload.limit == 2
    assert exc_info.value.payload.remaining == 0
    assert exc_info.value.payload.reset_at == datetime(
        2026,
        6,
        20,
        12,
        35,
        tzinfo=UTC,
    )


@pytest.mark.asyncio
async def test_rollback_session_create_decrements_counter() -> None:
    store = InMemoryStore()
    settings = Settings(demo_safety_enabled=True)
    policy = DemoSafetyPolicy(settings=settings, store=store)
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)

    receipt = await policy.consume_session_create("198.51.100.13", now=now)

    status_before_rollback = await policy.status_for_ip("198.51.100.13", now=now)
    assert status_before_rollback.caller_session_creates_used == 1

    await policy.rollback_session_create(receipt)

    status_after_rollback = await policy.status_for_ip("198.51.100.13", now=now)
    assert status_after_rollback.caller_session_creates_used == 0


@pytest.mark.asyncio
async def test_rollback_live_run_decrements_global_and_ip_counters() -> None:
    store = InMemoryStore()
    settings = Settings(demo_safety_enabled=True, live_demo_enabled=True)
    policy = DemoSafetyPolicy(settings=settings, store=store)
    now = datetime(2026, 6, 20, 12, tzinfo=UTC)

    receipt = await policy.consume_live_run("198.51.100.14", now=now)

    status_before_rollback = await policy.status_for_ip("198.51.100.14", now=now)
    assert status_before_rollback.global_live_runs_used == 1
    assert status_before_rollback.caller_live_runs_used == 1

    await policy.rollback_live_run(receipt)

    status_after_rollback = await policy.status_for_ip("198.51.100.14", now=now)
    assert status_after_rollback.global_live_runs_used == 0
    assert status_after_rollback.caller_live_runs_used == 0


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


@pytest.mark.asyncio
async def test_redis_demo_limit_store_handles_counters_expiry_and_text() -> None:
    redis = FakeRedis()
    redis.values["bytes-int"] = b"7"
    redis.values["plain-int"] = 8
    redis.values["bytes-text"] = b"enabled"
    store = RedisDemoLimitStore(redis)

    assert await store.get_int("missing") == 0
    assert await store.get_int("bytes-int") == 7
    assert await store.get_int("plain-int") == 8

    assert await store.increment("counter", 60) == 1
    assert redis.expirations["counter"] == 60
    assert await store.increment("counter", 120) == 2
    assert redis.expirations["counter"] == 60

    assert await store.decrement("empty-counter") == 0
    assert redis.values["empty-counter"] == 0

    assert await store.get_text("bytes-text") == "enabled"
    await store.set_text("flag", "true")
    assert redis.values["flag"] == "true"
    assert await store.get_text("flag") == "true"
