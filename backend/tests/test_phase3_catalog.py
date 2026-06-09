from puppyrun_agent.catalog import (
    build_custom_source_profile,
    custom_candidate_from_draft,
    registry_by_slug,
    select_candidates,
)


def test_phase3_registry_contains_built_in_source_profiles() -> None:
    registry = registry_by_slug()

    assert list(registry) == [
        "langgraph",
        "openai_agents_sdk",
        "crewai",
        "autogen",
        "dify",
    ]
    for slug in registry:
        profile = registry[slug]
        assert profile.official_docs_urls
        assert profile.docs_domains
        assert profile.blog_queries
        assert profile.stackexchange_tags
        assert profile.arxiv_queries
        assert profile.hn_queries
        assert profile.reddit_queries


def test_custom_candidate_source_profile_defaults_from_name_slug_and_repo() -> None:
    custom = custom_candidate_from_draft(
        "custom-agent",
        {
            "name": "Custom Agent",
            "repo_full_name": "example/custom-agent",
            "reason": "Team asked to compare this custom candidate.",
        },
    )

    assert custom.official_docs_urls == ()
    assert custom.docs_domains == ()
    assert custom.blog_queries == (
        "Custom Agent agent framework",
        "example/custom-agent issues",
        "Custom Agent risk",
    )
    assert custom.stackexchange_tags == ("custom-agent",)
    assert custom.arxiv_queries == ("Custom Agent agent framework",)
    assert custom.hn_queries == ("Custom Agent agent framework", "example/custom-agent")
    assert custom.reddit_queries == ("Custom Agent agent framework", "Custom Agent risk")


def test_build_custom_source_profile_handles_missing_repo() -> None:
    profile = build_custom_source_profile("Custom Agent", "custom-agent", "")

    assert profile.blog_queries == ("Custom Agent agent framework", "Custom Agent risk")
    assert profile.hn_queries == ("Custom Agent agent framework",)


def test_phase1_default_selection_remains_capped_to_three_candidates() -> None:
    selected = select_candidates({"mentioned_candidates": ["dify", "autogen"]})

    assert [candidate.slug for candidate in selected] == ["dify", "autogen", "langgraph"]
    assert len(selected) == 3
