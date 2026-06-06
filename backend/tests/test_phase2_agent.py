from puppyrun_agent.catalog import custom_candidate_from_draft, registry_by_slug
from puppyrun_agent.criteria import apply_weight_overrides, generate_criteria
from puppyrun_agent.github_client import RepositorySummary
from puppyrun_agent.phase2 import (
    apply_phase2_constraints,
    build_adr,
    build_gap_analysis,
    build_phase2_candidates,
    build_research_plan,
    build_score_cells,
    normalize_phase2_draft,
)
from puppyrun_agent.recommendation import build_weighted_recommendation


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


def test_candidate_draft_helpers_include_custom_exclude_and_lock_candidates() -> None:
    registry = registry_by_slug()
    assert registry["langgraph"].repo_full_name == "langchain-ai/langgraph"

    custom = custom_candidate_from_draft(
        "autogen",
        {
            "name": "AutoGen",
            "repo_full_name": "microsoft/autogen",
            "reason": "Team asked to compare AutoGen.",
        },
    )
    assert custom.slug == "autogen"
    assert custom.name == "AutoGen"
    assert custom.repo_full_name == "microsoft/autogen"
    assert "Team asked" in custom.include_reason
    assert custom.capabilities == ("custom",)

    draft = normalize_phase2_draft(
        {
            "candidate_overrides": {
                "crewai": {"action": "must_exclude", "reason": "Too role-heavy."},
                "langgraph": {"action": "lock", "reason": "Baseline winner."},
            },
            "custom_candidates": {
                "autogen": {
                    "name": "AutoGen",
                    "repo_full_name": "microsoft/autogen",
                    "reason": "Team asked to compare AutoGen.",
                }
            },
        },
        source_version_id="version-1",
    )

    candidates = build_phase2_candidates(
        {"mentioned_candidates": ["langgraph", "crewai"]},
        draft,
    )

    slugs = [candidate["slug"] for candidate in candidates]
    assert slugs == ["langgraph", "autogen"]
    assert "crewai" not in slugs
    assert candidates[0]["selection_state"] == "locked"
    assert candidates[0]["is_locked"] is True


def test_constraints_and_weight_overrides_shape_weighted_scoring() -> None:
    context = apply_phase2_constraints(
        {"constraints": ["python"]},
        normalize_phase2_draft(
            {
                "must_include_constraints": {
                    "observability": {
                        "enabled": True,
                        "reason": "Auditable traces are required.",
                    },
                    "human_in_loop": {
                        "enabled": True,
                        "reason": "Approval gates are required.",
                    },
                }
            },
            source_version_id="version-1",
        ),
    )
    generated_criteria = generate_criteria(context)
    assert next(
        criterion.weight
        for criterion in generated_criteria
        if criterion.name == "Observability and traceability"
    ) == 20
    assert next(
        criterion.weight
        for criterion in generated_criteria
        if criterion.name == "Human-in-the-loop fit"
    ) == 20

    criteria = apply_weight_overrides(
        generated_criteria,
        {
            "Observability and traceability": {
                "weight": 50,
                "reason": "Traceability is the main decision driver.",
            },
            "Runtime control and state": {
                "weight": 10,
                "reason": "Runtime can be handled later.",
            },
        },
    )

    assert "observability" in context["constraints"]
    assert next(
        criterion.weight
        for criterion in criteria
        if criterion.name == "Observability and traceability"
    ) == 50
    assert next(
        criterion.phase2_weight_reason
        for criterion in criteria
        if criterion.name == "Runtime control and state"
    ) == "Runtime can be handled later."

    summary, rationale = build_weighted_recommendation(
        [
            registry_by_slug()["langgraph"],
            registry_by_slug()["openai_agents_sdk"],
        ],
        criteria,
        {
            "langgraph": _repo(
                "langchain-ai/langgraph",
                stars=14000,
                license_spdx_id="MIT",
            ),
            "openai_agents_sdk": _repo(
                "openai/openai-agents-python",
                stars=25000,
                license_spdx_id="MIT",
            ),
        },
        context,
        version_number=2,
    )

    assert summary.startswith("Recommended v2: OpenAI Agents SDK.")
    assert rationale["recommended_slug"] == "openai_agents_sdk"
    assert [candidate["slug"] for candidate in rationale["ranked_candidates"]] == [
        "openai_agents_sdk",
        "langgraph",
    ]
    openai_rank = rationale["ranked_candidates"][0]
    assert openai_rank["weighted_score"] > rationale["ranked_candidates"][1]["weighted_score"]
    assert openai_rank["score_breakdown"]["Observability and traceability"]["weight"] == 50
    assert openai_rank["score_breakdown"]["Observability and traceability"]["score"] == 100

    summary_from_dicts, rationale_from_dicts = build_weighted_recommendation(
        build_phase2_candidates(
            {"mentioned_candidates": ["langgraph", "openai_agents_sdk"]},
            normalize_phase2_draft({}, source_version_id="version-1"),
        ),
        criteria,
        {
            "langgraph": _repo(
                "langchain-ai/langgraph",
                stars=14000,
                license_spdx_id="MIT",
            ),
            "openai_agents_sdk": _repo(
                "openai/openai-agents-python",
                stars=25000,
                license_spdx_id="MIT",
            ),
        },
        context,
        version_number=2,
    )

    assert summary_from_dicts == summary
    assert [
        (candidate["slug"], candidate["weighted_score"])
        for candidate in rationale_from_dicts["ranked_candidates"]
    ] == [
        (candidate["slug"], candidate["weighted_score"])
        for candidate in rationale["ranked_candidates"]
    ]


def test_score_cells_cover_each_candidate_criterion_with_evidence_links() -> None:
    candidates = [
        {"slug": "langgraph", "name": "LangGraph", "repo_full_name": "langchain-ai/langgraph"},
        {
            "slug": "openai_agents_sdk",
            "name": "OpenAI Agents SDK",
            "repo_full_name": "openai/openai-agents-python",
        },
    ]
    criteria = apply_weight_overrides(
        generate_criteria({"constraints": ["checkpointing", "observability"]}),
        {"Observability and traceability": {"weight": 40, "reason": "Main driver."}},
    )
    evidence_by_candidate = {
        "langgraph": [
            {
                "source_type": "github_repo",
                "source_url": "https://github.com/langchain-ai/langgraph",
                "label": "GitHub repository metadata",
            }
        ],
        "openai_agents_sdk": [
            {
                "source_type": "github_repo",
                "source_url": "https://github.com/openai/openai-agents-python",
                "label": "GitHub repository metadata",
            }
        ],
    }

    cells = build_score_cells(
        candidates,
        criteria,
        {
            "langgraph": _repo("langchain-ai/langgraph", stars=14000),
            "openai_agents_sdk": _repo("openai/openai-agents-python", stars=25000),
        },
        evidence_by_candidate,
    )

    assert len(cells) == len(candidates) * len(criteria)
    for cell in cells:
        assert cell["candidate_slug"] in {"langgraph", "openai_agents_sdk"}
        assert cell["criterion_name"]
        assert cell["status"] in {"strong", "partial", "weak", "unknown"}
        assert isinstance(cell["score"], int)
        assert cell["explanation"]
        assert cell["evidence_refs"]
        assert cell["evidence_refs"][0]["source_type"] == "github_repo"


def test_adr_builder_returns_required_sections_and_evidence_links() -> None:
    score_cells = [
        {
            "candidate_slug": "langgraph",
            "criterion_name": "Runtime control and state",
            "status": "strong",
            "score": 95,
            "explanation": "Strong checkpointing support.",
            "evidence_refs": [
                {
                    "label": "LangGraph GitHub",
                    "source_url": "https://github.com/langchain-ai/langgraph",
                }
            ],
        }
    ]

    adr = build_adr(
        2,
        "Recommended v2: LangGraph.",
        {
            "recommended_slug": "langgraph",
            "ranked_candidates": [
                {
                    "slug": "langgraph",
                    "name": "LangGraph",
                    "weighted_score": 91,
                    "reasons": ["matches checkpointing requirement"],
                }
            ],
        },
        {
            "changed_candidates": ["autogen"],
            "changed_constraints": ["checkpointing"],
            "changed_weights": ["Runtime control and state"],
            "research_tasks": [
                {
                    "candidate_slug": "autogen",
                    "repo_full_name": "microsoft/autogen",
                    "reason": "missing_github_evidence",
                }
            ],
        },
        score_cells,
    )

    assert adr["title"] == "ADR v2: Recommended v2: LangGraph."
    body = adr["body"]
    for section in [
        "## Context",
        "## Decision",
        "## Options",
        "## Rationale",
        "## Tradeoffs",
        "## Risks",
        "## Evidence links",
    ]:
        assert section in body
    assert "https://github.com/langchain-ai/langgraph" in body
    assert "microsoft/autogen" in body


def _repo(
    full_name: str,
    *,
    stars: int = 1000,
    license_spdx_id: str | None = "MIT",
) -> RepositorySummary:
    return RepositorySummary(
        full_name=full_name,
        source_url=f"https://github.com/{full_name}",
        description=f"{full_name} repository",
        stars=stars,
        forks=100,
        open_issues=25,
        pushed_at="2026-06-01T00:00:00Z",
        license_spdx_id=license_spdx_id,
    )
