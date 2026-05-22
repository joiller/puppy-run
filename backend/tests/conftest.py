import pytest
from httpx import ASGITransport, AsyncClient

from puppyrun_api.main import create_app


@pytest.fixture
async def api_client() -> AsyncClient:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
