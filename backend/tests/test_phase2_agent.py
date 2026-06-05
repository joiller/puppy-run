from puppyrun_agent.phase2 import (
    apply_phase2_constraints,
    build_gap_analysis,
    build_phase2_candidates,
    build_research_plan,
    normalize_phase2_draft,
)


def test_gap_analysis_distinguishes_weight_only_from_added_candidate() -> None:
    context = {
        "constraints": ["typescript"],
        "mentioned_candidates": ["langgraph", "crewai"],
    }
    baseline_candidates = [
        {
            "name": "LangGraph",
            "slug": "langgraph",
            "repo_full_name": "langchain-ai/langgraph",
        },
        {
            "name": "CrewAI",
            "slug": "crewai",
            "repo_full_name": "crewAIInc/crewAI",
        },
    ]
    criteria = [{"name": "Runtime control and state", "weight": 30}]
    previous_evidence = [
        {
            "candidate_slug": "langgraph",
            "repo_full_name": "langchain-ai/langgraph",
            "source_type": "github_repo",
        },
        {
            "candidate_slug": "crewai",
            "repo_full_name": "crewAIInc/crewAI",
            "source_type": "github_repo",
        },
    ]

    weight_only_draft = normalize_phase2_draft(
        {
            "weight_overrides": {
                "Runtime control and state": {
                    "weight": 55,
                    "reason": "Recovery matters most.",
                }
            }
        },
        source_version_id="version-1",
    )
    weight_only_gap = build_gap_analysis(
        weight_only_draft,
        baseline_candidates,
        criteria,
        previous_evidence,
    )

    assert weight_only_gap["requires_research"] is False
    assert weight_only_gap["requires_github_fetch"] is False
    assert weight_only_gap["score_only"] is True
    assert weight_only_gap["changed_weights"] == ["Runtime control and state"]
    assert weight_only_gap["research_tasks"] == []

    expanded_draft = normalize_phase2_draft(
        {
            "candidate_overrides": {
                "crewai": {
                    "action": "must_exclude",
                    "reason": "Team does not want role-based orchestration.",
                }
            },
            "custom_candidates": {
                "autogen": {
                    "name": "AutoGen",
                    "slug": "autogen",
                    "repo_full_name": "microsoft/autogen",
                    "reason": "Team asked to compare AutoGen.",
                }
            },
            "must_include_constraints": {
                "checkpointing": {
                    "enabled": True,
                    "reason": "Checkpointing is mandatory.",
                }
            },
            "must_exclude_constraints": {
                "typescript": {
                    "enabled": True,
                    "reason": "Team wants Python-first tooling.",
                }
            },
        },
        source_version_id="version-1",
    )

    effective_context = apply_phase2_constraints(context, expanded_draft)
    assert "checkpointing" in effective_context["constraints"]
    assert "typescript" not in effective_context["constraints"]
    assert effective_context["phase2_must_exclude_constraints"] == ["typescript"]

    next_candidates = build_phase2_candidates(context, expanded_draft)
    assert [candidate["slug"] for candidate in next_candidates] == ["langgraph", "autogen"]

    research_plan = build_research_plan(next_candidates, previous_evidence)
    assert research_plan["research_tasks"] == [
        {
            "candidate_slug": "autogen",
            "repo_full_name": "microsoft/autogen",
            "reason": "missing_github_evidence",
        }
    ]
    assert research_plan["reuse_tasks"] == [
        {
            "candidate_slug": "langgraph",
            "repo_full_name": "langchain-ai/langgraph",
            "reason": "existing_github_evidence",
        }
    ]

    expanded_gap = build_gap_analysis(
        expanded_draft,
        next_candidates,
        criteria,
        previous_evidence,
    )

    assert expanded_gap["requires_research"] is True
    assert expanded_gap["score_only"] is False
    assert expanded_gap["changed_candidates"] == ["autogen", "crewai"]
    assert expanded_gap["changed_constraints"] == ["checkpointing", "typescript"]
    assert expanded_gap["research_tasks"] == [
        {
            "candidate_slug": "autogen",
            "repo_full_name": "microsoft/autogen",
            "reason": "missing_github_evidence",
        }
    ]
