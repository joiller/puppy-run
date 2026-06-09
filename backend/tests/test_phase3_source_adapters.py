import httpx
import pytest

from puppyrun_agent.github_client import GitHubClient
from puppyrun_agent.source_adapters import (
    ArxivAdapter,
    DirectDocsAdapter,
    GitHubIssueReleaseAdapter,
    HackerNewsSearchAdapter,
    RedditAdapter,
    StackExchangeAdapter,
    TavilySearchAdapter,
)


@pytest.mark.asyncio
async def test_github_issue_release_adapter_normalizes_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/langchain-ai/langgraph/issues":
            return httpx.Response(
                200,
                json=[
                    {
                        "html_url": "https://github.com/langchain-ai/langgraph/issues/1",
                        "title": "Checkpointing issue",
                        "body": "Users discuss checkpointing recovery.",
                        "state": "open",
                        "comments": 3,
                        "pull_request": None,
                    },
                    {
                        "html_url": "https://github.com/langchain-ai/langgraph/pull/2",
                        "title": "Ignore PR",
                        "pull_request": {"url": "https://api.github.com/pulls/2"},
                    },
                ],
            )
        if request.url.path == "/repos/langchain-ai/langgraph/releases":
            return httpx.Response(
                200,
                json=[
                    {
                        "html_url": "https://github.com/langchain-ai/langgraph/releases/v1",
                        "name": "v1.0",
                        "body": "Stable release notes.",
                        "tag_name": "v1.0",
                    }
                ],
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    transport = httpx.MockTransport(handler)
    async with GitHubClient(transport=transport) as github:
        results = await GitHubIssueReleaseAdapter(github).collect(
            candidate_slug="langgraph",
            repo_full_name="langchain-ai/langgraph",
        )

    assert [result.source_type for result in results] == ["github_issue", "github_release"]
    assert results[0].source_url == "https://github.com/langchain-ai/langgraph/issues/1"
    assert results[0].candidate_slug == "langgraph"
    assert results[0].credibility == "medium"
    assert "Checkpointing issue" in results[0].title
    assert results[1].source_url == "https://github.com/langchain-ai/langgraph/releases/v1"


@pytest.mark.asyncio
async def test_direct_docs_adapter_stores_short_normalized_content() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            text=(
                "<html><head><title>LangGraph docs</title></head>"
                "<body><h1>LangGraph</h1><p>Checkpointing docs.</p></body></html>"
            ),
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        results = await DirectDocsAdapter(client).collect(
            candidate_slug="langgraph",
            urls=["https://docs.example/langgraph"],
        )

    assert len(results) == 1
    result = results[0]
    assert result.source_type == "official_docs"
    assert result.source_url == "https://docs.example/langgraph"
    assert result.title == "LangGraph docs"
    assert result.credibility == "high"
    assert result.content_hash
    assert "Checkpointing docs" in result.summary
    assert "body" not in result.metadata


@pytest.mark.asyncio
async def test_tavily_adapter_skips_without_api_key_and_completes_with_mock_key() -> None:
    skipped = await TavilySearchAdapter(api_key=None).search(
        candidate_slug="langgraph",
        query="LangGraph checkpointing",
        source_type="technical_blog",
    )
    assert skipped == []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://blog.example/langgraph",
                        "title": "LangGraph in production",
                        "content": "Production lessons for LangGraph.",
                        "score": 0.8,
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        results = await TavilySearchAdapter(api_key="test-key", client=client).search(
            candidate_slug="langgraph",
            query="LangGraph checkpointing",
            source_type="technical_blog",
        )

    assert [result.source_type for result in results] == ["technical_blog"]
    assert results[0].source_url == "https://blog.example/langgraph"
    assert results[0].credibility == "medium"


@pytest.mark.asyncio
async def test_hacker_news_adapter_uses_tavily_site_restricted_search() -> None:
    seen_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payloads.append(request.read().decode())
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://news.ycombinator.com/item?id=1",
                        "title": "HN discussion",
                        "content": "Community concern.",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        results = await HackerNewsSearchAdapter(
            TavilySearchAdapter(api_key="test-key", client=client)
        ).search(candidate_slug="langgraph", query="LangGraph risk")

    assert "site:news.ycombinator.com" in seen_payloads[0]
    assert results[0].source_type == "hacker_news"
    assert results[0].credibility == "low"


@pytest.mark.asyncio
async def test_stack_exchange_adapter_normalizes_advanced_search_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2.3/search/advanced"
        assert request.url.params["tagged"] == "langgraph"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "link": "https://stackoverflow.com/questions/1",
                        "title": "LangGraph question",
                        "score": 4,
                        "answer_count": 2,
                        "tags": ["langgraph"],
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://api.stackexchange.com",
    ) as client:
        results = await StackExchangeAdapter(client).search(
            candidate_slug="langgraph",
            tags=("langgraph",),
            query="checkpointing",
        )

    assert results[0].source_type == "stack_exchange"
    assert results[0].source_url == "https://stackoverflow.com/questions/1"
    assert results[0].credibility == "low"


@pytest.mark.asyncio
async def test_arxiv_adapter_normalizes_atom_feed_results() -> None:
    atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/1234.5678</id>
        <title>Agent Frameworks</title>
        <summary>Paper summary about agent frameworks.</summary>
      </entry>
    </feed>
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=atom))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://export.arxiv.org",
    ) as client:
        results = await ArxivAdapter(client).search(
            candidate_slug="langgraph",
            query="agent framework",
        )

    assert results[0].source_type == "arxiv"
    assert results[0].source_url == "http://arxiv.org/abs/1234.5678"
    assert results[0].credibility == "medium"
    assert "Paper summary" in results[0].summary


@pytest.mark.asyncio
async def test_reddit_adapter_skips_by_default() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        results = await RedditAdapter(enabled=False, client=client).search(
            candidate_slug="langgraph",
            query="LangGraph risk",
        )

    assert results == []
    assert called is False
