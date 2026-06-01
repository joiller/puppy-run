from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class RepositorySummary:
    full_name: str
    source_url: str
    description: str
    stars: int
    forks: int
    open_issues: int
    pushed_at: str
    license_spdx_id: str | None

    def to_evidence_payload(self) -> dict[str, Any]:
        return {
            "full_name": self.full_name,
            "stars": self.stars,
            "forks": self.forks,
            "open_issues": self.open_issues,
            "pushed_at": self.pushed_at,
            "license_spdx_id": self.license_spdx_id,
        }


class GitHubClient:
    def __init__(
        self,
        *,
        api_base_url: str = "https://api.github.com",
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        self._client = httpx.AsyncClient(
            base_url=api_base_url,
            headers=headers,
            timeout=10.0,
            transport=transport,
        )

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_repository_summary(self, repo_full_name: str) -> RepositorySummary:
        response = await self._client.get(f"/repos/{repo_full_name}")
        if response.status_code == 404:
            raise ValueError(f"GitHub repository not found: {repo_full_name}")
        response.raise_for_status()

        payload = response.json()
        license_payload = payload.get("license") or {}

        return RepositorySummary(
            full_name=payload["full_name"],
            source_url=payload["html_url"],
            description=payload.get("description") or "",
            stars=int(payload.get("stargazers_count") or 0),
            forks=int(payload.get("forks_count") or 0),
            open_issues=int(payload.get("open_issues_count") or 0),
            pushed_at=payload.get("pushed_at") or "",
            license_spdx_id=license_payload.get("spdx_id"),
        )
