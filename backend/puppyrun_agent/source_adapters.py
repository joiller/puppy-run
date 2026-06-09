import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html import unescape
from typing import Any

import httpx

from puppyrun_agent.github_client import GitHubClient
from puppyrun_agent.tool_runtime import content_hash

MAX_SUMMARY_LENGTH = 500


@dataclass(frozen=True)
class EvidenceSourceResult:
    source_type: str
    source_url: str
    title: str
    summary: str
    citation_text: str
    credibility: str
    candidate_slug: str
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            object.__setattr__(
                self,
                "content_hash",
                content_hash(
                    {
                        "source_type": self.source_type,
                        "source_url": self.source_url,
                        "title": self.title,
                        "summary": self.summary,
                        "citation_text": self.citation_text,
                    }
                ),
            )


class GitHubIssueReleaseAdapter:
    def __init__(self, github: GitHubClient, *, max_results: int = 5) -> None:
        self.github = github
        self.max_results = max_results

    async def collect(
        self,
        *,
        candidate_slug: str,
        repo_full_name: str,
    ) -> list[EvidenceSourceResult]:
        issues = await self.github.fetch_issue_signals(repo_full_name, limit=self.max_results)
        releases = await self.github.fetch_release_signals(repo_full_name, limit=self.max_results)
        results: list[EvidenceSourceResult] = []
        for issue in issues:
            results.append(
                EvidenceSourceResult(
                    source_type="github_issue",
                    source_url=issue["source_url"],
                    title=issue["title"],
                    summary=_shorten(issue["summary"]),
                    citation_text=issue["title"],
                    credibility="medium",
                    candidate_slug=candidate_slug,
                    metadata={
                        "state": issue.get("state"),
                        "comments": issue.get("comments", 0),
                        "repo_full_name": repo_full_name,
                    },
                )
            )
        for release in releases:
            results.append(
                EvidenceSourceResult(
                    source_type="github_release",
                    source_url=release["source_url"],
                    title=release["title"],
                    summary=_shorten(release["summary"]),
                    citation_text=release["title"],
                    credibility="high",
                    candidate_slug=candidate_slug,
                    metadata={
                        "tag_name": release.get("tag_name"),
                        "repo_full_name": repo_full_name,
                    },
                )
            )
        return results


class DirectDocsAdapter:
    def __init__(self, client: httpx.AsyncClient, *, max_results: int = 5) -> None:
        self.client = client
        self.max_results = max_results

    async def collect(
        self,
        *,
        candidate_slug: str,
        urls: tuple[str, ...] | list[str],
    ) -> list[EvidenceSourceResult]:
        results = []
        for url in list(urls)[: self.max_results]:
            response = await self.client.get(url)
            response.raise_for_status()
            title, text = _extract_html_title_and_text(response.text)
            summary = _shorten(text)
            results.append(
                EvidenceSourceResult(
                    source_type="official_docs",
                    source_url=str(response.url),
                    title=title or str(response.url),
                    summary=summary,
                    citation_text=summary,
                    credibility="high",
                    candidate_slug=candidate_slug,
                    metadata={"status_code": response.status_code},
                )
            )
        return results


class TavilySearchAdapter:
    def __init__(
        self,
        *,
        api_key: str | None,
        client: httpx.AsyncClient | None = None,
        max_results: int = 5,
    ) -> None:
        self.api_key = api_key
        self.client = client
        self.max_results = max_results

    async def search(
        self,
        *,
        candidate_slug: str,
        query: str,
        source_type: str,
        credibility: str = "medium",
    ) -> list[EvidenceSourceResult]:
        if not self.api_key:
            return []
        close_client = False
        client = self.client
        if client is None:
            client = httpx.AsyncClient(base_url="https://api.tavily.com")
            close_client = True
        try:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"query": query, "max_results": self.max_results},
            )
            response.raise_for_status()
            payload = response.json()
        finally:
            if close_client:
                await client.aclose()

        return [
            EvidenceSourceResult(
                source_type=source_type,
                source_url=item.get("url") or "",
                title=item.get("title") or item.get("url") or query,
                summary=_shorten(item.get("content") or item.get("summary") or ""),
                citation_text=_shorten(item.get("content") or item.get("title") or ""),
                credibility=credibility,
                candidate_slug=candidate_slug,
                metadata={"score": item.get("score"), "query": query},
            )
            for item in payload.get("results", [])[: self.max_results]
            if item.get("url")
        ]


class HackerNewsSearchAdapter:
    def __init__(self, tavily: TavilySearchAdapter) -> None:
        self.tavily = tavily

    async def search(self, *, candidate_slug: str, query: str) -> list[EvidenceSourceResult]:
        return await self.tavily.search(
            candidate_slug=candidate_slug,
            query=f"{query} site:news.ycombinator.com",
            source_type="hacker_news",
            credibility="low",
        )


class StackExchangeAdapter:
    def __init__(self, client: httpx.AsyncClient, *, max_results: int = 5) -> None:
        self.client = client
        self.max_results = max_results

    async def search(
        self,
        *,
        candidate_slug: str,
        tags: tuple[str, ...],
        query: str,
    ) -> list[EvidenceSourceResult]:
        if not tags:
            return []
        response = await self.client.get(
            "/2.3/search/advanced",
            params={
                "order": "desc",
                "sort": "relevance",
                "site": "stackoverflow",
                "tagged": ";".join(tags),
                "q": query,
                "pagesize": self.max_results,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return [
            EvidenceSourceResult(
                source_type="stack_exchange",
                source_url=item.get("link") or "",
                title=item.get("title") or query,
                summary=_shorten(
                    f"Score {item.get('score', 0)}, {item.get('answer_count', 0)} answers."
                ),
                citation_text=item.get("title") or query,
                credibility="low",
                candidate_slug=candidate_slug,
                metadata={
                    "score": item.get("score", 0),
                    "answer_count": item.get("answer_count", 0),
                    "tags": item.get("tags", []),
                },
            )
            for item in payload.get("items", [])[: self.max_results]
            if item.get("link")
        ]


class ArxivAdapter:
    def __init__(self, client: httpx.AsyncClient, *, max_results: int = 5) -> None:
        self.client = client
        self.max_results = max_results

    async def search(self, *, candidate_slug: str, query: str) -> list[EvidenceSourceResult]:
        response = await self.client.get(
            "/api/query",
            params={"search_query": f"all:{query}", "start": 0, "max_results": self.max_results},
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("atom:entry", namespace)[: self.max_results]:
            source_url = _entry_text(entry, "id", namespace)
            title = _entry_text(entry, "title", namespace)
            summary = _shorten(_entry_text(entry, "summary", namespace))
            results.append(
                EvidenceSourceResult(
                    source_type="arxiv",
                    source_url=source_url,
                    title=title,
                    summary=summary,
                    citation_text=title,
                    credibility="medium",
                    candidate_slug=candidate_slug,
                    metadata={"query": query},
                )
            )
        return results


class RedditAdapter:
    def __init__(
        self,
        *,
        enabled: bool,
        client: httpx.AsyncClient | None = None,
        max_results: int = 5,
    ) -> None:
        self.enabled = enabled
        self.client = client
        self.max_results = max_results

    async def search(self, *, candidate_slug: str, query: str) -> list[EvidenceSourceResult]:
        if not self.enabled:
            return []
        if self.client is None:
            return []
        response = await self.client.get(
            "https://www.reddit.com/search.json",
            params={"q": query, "limit": self.max_results},
        )
        response.raise_for_status()
        payload = response.json()
        children = payload.get("data", {}).get("children", [])
        return [
            EvidenceSourceResult(
                source_type="reddit",
                source_url=f"https://www.reddit.com{post.get('permalink', '')}",
                title=post.get("title") or query,
                summary=_shorten(post.get("selftext") or post.get("title") or ""),
                citation_text=post.get("title") or query,
                credibility="low",
                candidate_slug=candidate_slug,
                metadata={"subreddit": post.get("subreddit"), "score": post.get("score")},
            )
            for child in children[: self.max_results]
            for post in [child.get("data", {})]
            if post.get("permalink")
        ]


def _extract_html_title_and_text(html: str) -> tuple[str, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = _clean_text(title_match.group(1)) if title_match else ""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return title, _clean_text(text)


def _entry_text(entry: ET.Element, tag: str, namespace: dict[str, str]) -> str:
    element = entry.find(f"atom:{tag}", namespace)
    return _clean_text(element.text if element is not None else "")


def _shorten(value: str, *, limit: int = MAX_SUMMARY_LENGTH) -> str:
    cleaned = _clean_text(value)
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}...[truncated]"


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()
