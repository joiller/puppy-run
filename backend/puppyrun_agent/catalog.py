from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateSourceProfile:
    official_docs_urls: tuple[str, ...]
    docs_domains: tuple[str, ...]
    blog_queries: tuple[str, ...]
    stackexchange_tags: tuple[str, ...]
    arxiv_queries: tuple[str, ...]
    hn_queries: tuple[str, ...]
    reddit_queries: tuple[str, ...]


@dataclass(frozen=True)
class CandidateProfile:
    name: str
    slug: str
    repo_full_name: str
    capabilities: tuple[str, ...]
    include_reason: str
    official_docs_urls: tuple[str, ...] = ()
    docs_domains: tuple[str, ...] = ()
    blog_queries: tuple[str, ...] = ()
    stackexchange_tags: tuple[str, ...] = ()
    arxiv_queries: tuple[str, ...] = ()
    hn_queries: tuple[str, ...] = ()
    reddit_queries: tuple[str, ...] = ()


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
        official_docs_urls=("https://langchain-ai.github.io/langgraph/",),
        docs_domains=("langchain-ai.github.io", "langchain.com"),
        blog_queries=("LangGraph checkpointing agent framework", "LangGraph production risk"),
        stackexchange_tags=("langchain", "langgraph"),
        arxiv_queries=("LangGraph agent framework", "stateful multi-agent graph"),
        hn_queries=("LangGraph agent framework", "langchain-ai/langgraph"),
        reddit_queries=("LangGraph agent framework", "LangGraph risk"),
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
        official_docs_urls=("https://openai.github.io/openai-agents-python/",),
        docs_domains=("openai.github.io", "platform.openai.com"),
        blog_queries=("OpenAI Agents SDK handoffs tracing", "OpenAI Agents SDK risk"),
        stackexchange_tags=("openai-api", "openai-agents"),
        arxiv_queries=("OpenAI Agents SDK agent framework",),
        hn_queries=("OpenAI Agents SDK", "openai/openai-agents-python"),
        reddit_queries=("OpenAI Agents SDK", "OpenAI Agents SDK risk"),
    ),
    CandidateProfile(
        name="CrewAI",
        slug="crewai",
        repo_full_name="crewAIInc/crewAI",
        capabilities=("python", "multi_agent_roles", "task_orchestration"),
        include_reason=(
            "Included because it represents a role-and-task oriented multi-agent workflow style."
        ),
        official_docs_urls=("https://docs.crewai.com/",),
        docs_domains=("docs.crewai.com", "crewai.com"),
        blog_queries=("CrewAI multi-agent framework", "CrewAI maintenance risk"),
        stackexchange_tags=("crewai", "python"),
        arxiv_queries=("CrewAI multi-agent framework",),
        hn_queries=("CrewAI agent framework", "crewAIInc/crewAI"),
        reddit_queries=("CrewAI agent framework", "CrewAI risk"),
    ),
    CandidateProfile(
        name="AutoGen",
        slug="autogen",
        repo_full_name="microsoft/autogen",
        capabilities=("python", "multi_agent_conversation", "tool_calling", "orchestration"),
        include_reason=(
            "Included because it is a mature Microsoft-backed multi-agent conversation "
            "framework with broad examples and integrations."
        ),
        official_docs_urls=("https://microsoft.github.io/autogen/",),
        docs_domains=("microsoft.github.io", "microsoft.com"),
        blog_queries=("AutoGen multi-agent framework", "AutoGen agent risk"),
        stackexchange_tags=("autogen", "python"),
        arxiv_queries=("AutoGen multi-agent framework",),
        hn_queries=("AutoGen agent framework", "microsoft/autogen"),
        reddit_queries=("AutoGen agent framework", "AutoGen risk"),
    ),
    CandidateProfile(
        name="Dify",
        slug="dify",
        repo_full_name="langgenius/dify",
        capabilities=("workflow_builder", "rag", "self_hosted", "llmops"),
        include_reason=(
            "Included because it represents a self-hostable visual LLM application "
            "and workflow platform adjacent to agent workbenches."
        ),
        official_docs_urls=("https://docs.dify.ai/",),
        docs_domains=("docs.dify.ai", "dify.ai"),
        blog_queries=("Dify LLM workflow platform", "Dify self hosted risk"),
        stackexchange_tags=("dify", "llm"),
        arxiv_queries=("Dify LLM workflow platform",),
        hn_queries=("Dify LLM workflow", "langgenius/dify"),
        reddit_queries=("Dify LLM workflow", "Dify risk"),
    ),
)


def registry_by_slug() -> dict[str, CandidateProfile]:
    return {candidate.slug: candidate for candidate in CANDIDATE_REGISTRY}


def custom_candidate_from_draft(slug: str, payload: dict) -> CandidateProfile:
    normalized_slug = _normalize_slug(payload.get("slug") or slug)
    name = _clean_text(payload.get("name")) or normalized_slug
    repo_full_name = _clean_text(payload.get("repo_full_name"))
    reason = _clean_text(payload.get("reason"))
    source_profile = build_custom_source_profile(name, normalized_slug, repo_full_name)
    return CandidateProfile(
        name=name,
        slug=normalized_slug,
        repo_full_name=repo_full_name,
        capabilities=("custom",),
        include_reason=reason or f"Included as a custom Phase 2 candidate: {name}.",
        **_source_profile_kwargs(source_profile),
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


def _tag_slug(value: object) -> str:
    return _normalize_slug(value).replace(" ", "-").replace("_", "-")


def build_custom_source_profile(
    name: str,
    slug: str,
    repo_full_name: str,
) -> CandidateSourceProfile:
    clean_name = _clean_text(name) or _clean_text(slug)
    clean_slug = _normalize_slug(slug)
    clean_repo = _clean_text(repo_full_name)
    base_query = f"{clean_name} agent framework"
    blog_queries = [base_query]
    if clean_repo:
        blog_queries.append(f"{clean_repo} issues")
    blog_queries.append(f"{clean_name} risk")

    hn_queries = [base_query]
    if clean_repo:
        hn_queries.append(clean_repo)

    return CandidateSourceProfile(
        official_docs_urls=(),
        docs_domains=(),
        blog_queries=tuple(blog_queries),
        stackexchange_tags=tuple(
            dict.fromkeys(tag for tag in (clean_slug, _tag_slug(clean_name)) if tag)
        ),
        arxiv_queries=(base_query,),
        hn_queries=tuple(hn_queries),
        reddit_queries=(base_query, f"{clean_name} risk"),
    )


def _source_profile_kwargs(source_profile: CandidateSourceProfile) -> dict:
    return {
        "official_docs_urls": source_profile.official_docs_urls,
        "docs_domains": source_profile.docs_domains,
        "blog_queries": source_profile.blog_queries,
        "stackexchange_tags": source_profile.stackexchange_tags,
        "arxiv_queries": source_profile.arxiv_queries,
        "hn_queries": source_profile.hn_queries,
        "reddit_queries": source_profile.reddit_queries,
    }
