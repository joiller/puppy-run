import httpx
import pytest

from puppyrun_agent.github_client import GitHubClient


@pytest.mark.asyncio
async def test_fetch_repository_summary_normalizes_github_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/langchain-ai/langgraph"
        return httpx.Response(
            200,
            json={
                "full_name": "langchain-ai/langgraph",
                "html_url": "https://github.com/langchain-ai/langgraph",
                "description": "Build resilient language agents as graphs.",
                "stargazers_count": 100,
                "forks_count": 20,
                "open_issues_count": 7,
                "pushed_at": "2026-05-20T12:00:00Z",
                "license": {"spdx_id": "MIT"},
            },
        )

    transport = httpx.MockTransport(handler)
    async with GitHubClient(transport=transport) as client:
        summary = await client.fetch_repository_summary("langchain-ai/langgraph")

    assert summary.full_name == "langchain-ai/langgraph"
    assert summary.stars == 100
    assert summary.license_spdx_id == "MIT"
    assert summary.source_url == "https://github.com/langchain-ai/langgraph"


@pytest.mark.asyncio
async def test_fetch_repository_summary_raises_for_missing_repo() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(404, json={"message": "Not Found"})
    )
    async with GitHubClient(transport=transport) as client:
        with pytest.raises(ValueError, match="GitHub repository not found"):
            await client.fetch_repository_summary("missing/repo")
