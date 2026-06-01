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


def select_candidates(context: dict) -> list[CandidateProfile]:
    registry_by_slug = {candidate.slug: candidate for candidate in CANDIDATE_REGISTRY}
    mentioned = [
        slug
        for slug in context.get("mentioned_candidates", [])
        if slug in registry_by_slug
    ]
    selected_slugs = set(mentioned)
    ordered = [registry_by_slug[slug] for slug in mentioned]
    ordered.extend(
        candidate for candidate in CANDIDATE_REGISTRY if candidate.slug not in selected_slugs
    )
    return ordered[:3]
