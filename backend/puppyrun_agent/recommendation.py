from dataclasses import dataclass

from puppyrun_agent.catalog import CandidateProfile
from puppyrun_agent.github_client import RepositorySummary


@dataclass(frozen=True)
class CandidateScore:
    slug: str
    total: int
    reasons: list[str]


def score_candidate(
    candidate: CandidateProfile,
    repo: RepositorySummary,
    context: dict,
) -> CandidateScore:
    constraints = set(context.get("constraints", []))
    score = 40
    reasons: list[str] = []

    if "checkpointing" in constraints and "checkpointing" in candidate.capabilities:
        score += 20
        reasons.append("matches checkpointing requirement")
    if "human_in_loop" in constraints and "human_in_loop" in candidate.capabilities:
        score += 15
        reasons.append("matches human-in-the-loop requirement")
    if "observability" in constraints and {"tracing", "guardrails"} & set(
        candidate.capabilities
    ):
        score += 10
        reasons.append("has tracing or guardrail-oriented capability signals")
    if repo.stars >= 20000:
        score += 10
        reasons.append("shows strong GitHub adoption")
    if repo.license_spdx_id:
        score += 5
        reasons.append(f"declares {repo.license_spdx_id} license metadata")

    return CandidateScore(slug=candidate.slug, total=min(score, 100), reasons=reasons)


def build_recommendation(
    candidates: list[tuple[CandidateProfile, RepositorySummary, CandidateScore]],
) -> tuple[str, dict]:
    if not candidates:
        raise ValueError("cannot build recommendation without candidates")

    ranked = sorted(candidates, key=lambda item: item[2].total, reverse=True)
    winner, repo, score = ranked[0]
    summary = (
        f"Recommended: {winner.name}. It scored {score.total}/100 in this Phase 1 "
        f"thin-slice analysis using GitHub health and runtime-fit criteria."
    )
    rationale = {
        "recommended_slug": winner.slug,
        "recommended_repo": repo.full_name,
        "ranked_candidates": [
            {
                "slug": candidate.slug,
                "name": candidate.name,
                "repo": repository.full_name,
                "score": candidate_score.total,
                "reasons": candidate_score.reasons,
            }
            for candidate, repository, candidate_score in ranked
        ],
    }
    return summary, rationale
