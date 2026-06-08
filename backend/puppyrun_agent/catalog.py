from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateProfile:
    name: str
    slug: str
    repo_full_name: str
    capabilities: tuple[str, ...]
    include_reason: str


CANDIDATE_REGISTRY: tuple[CandidateProfile, ...] = (
    CandidateProfile(
        name="LangGraph",
        slug="langgraph",
        repo_full_name="langchain-ai/langgraph",
        capabilities=("python", "typescript", "checkpointing", "human_in_loop", "stateful_graph"),
        include_reason=(
            "Included because it is designed for stateful graph-based Agent workflows "
            "and is a strong baseline for checkpointed runtime control."
        ),
    ),
    CandidateProfile(
        name="OpenAI Agents SDK",
        slug="openai_agents_sdk",
        repo_full_name="openai/openai-agents-python",
        capabilities=("python", "handoffs", "guardrails", "tracing", "tool_calling"),
        include_reason=(
            "Included because it is a lightweight Python SDK for agentic workflows, "
            "handoffs, guardrails, and tracing."
        ),
    ),
    CandidateProfile(
        name="CrewAI",
        slug="crewai",
        repo_full_name="crewAIInc/crewAI",
        capabilities=("python", "multi_agent_roles", "task_orchestration"),
        include_reason=(
            "Included because it represents a role-and-task oriented multi-agent workflow style."
        ),
    ),
)


def registry_by_slug() -> dict[str, CandidateProfile]:
    return {candidate.slug: candidate for candidate in CANDIDATE_REGISTRY}


def custom_candidate_from_draft(slug: str, payload: dict) -> CandidateProfile:
    normalized_slug = _normalize_slug(payload.get("slug") or slug)
    name = _clean_text(payload.get("name")) or normalized_slug
    repo_full_name = _clean_text(payload.get("repo_full_name"))
    reason = _clean_text(payload.get("reason"))
    return CandidateProfile(
        name=name,
        slug=normalized_slug,
        repo_full_name=repo_full_name,
        capabilities=("custom",),
        include_reason=reason or f"Included as a custom Phase 2 candidate: {name}.",
    )


def select_candidates(context: dict) -> list[CandidateProfile]:
    registry = registry_by_slug()
    mentioned = [
        slug
        for slug in context.get("mentioned_candidates", [])
        if slug in registry
    ]
    selected_slugs = set(mentioned)
    ordered = [registry[slug] for slug in mentioned]
    ordered.extend(
        candidate for candidate in CANDIDATE_REGISTRY if candidate.slug not in selected_slugs
    )
    return ordered[:3]


def _clean_text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_slug(value: object) -> str:
    return _clean_text(value).lower()
