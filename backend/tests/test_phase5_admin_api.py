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
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
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
