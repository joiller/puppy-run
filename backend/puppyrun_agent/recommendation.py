from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from puppyrun_agent.catalog import CandidateProfile, registry_by_slug
from puppyrun_agent.criteria import CriterionProfile
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


def build_weighted_recommendation(
    candidates: list[Any],
    criteria: list[CriterionProfile],
    repos: dict[str, RepositorySummary],
    context: dict,
    *,
    version_number: int,
) -> tuple[str, dict]:
    if not candidates:
        raise ValueError("cannot build recommendation without candidates")

    ranked = []
    has_phase3_risks = _has_phase3_risk_context(context)
    for candidate in candidates:
        slug = _candidate_slug(candidate)
        repo = _repo_for_candidate(candidate, repos)
        breakdown = {
            criterion.name: score_candidate_for_criterion(
                candidate,
                criterion,
                repo,
                context,
            )
            for criterion in criteria
        }
        total_weight = sum(max(criterion.weight, 0) for criterion in criteria)
        weighted_total = 0
        if total_weight:
            weighted_total = round(
                sum(
                    breakdown[criterion.name]["score"] * max(criterion.weight, 0)
                    for criterion in criteria
                )
                / total_weight
            )
        risk_summary = _phase3_risk_summary(slug, context) if has_phase3_risks else {}
        risk_adjustment = int(risk_summary.get("risk_adjustment", 0))
        adjusted_total = max(0, weighted_total + risk_adjustment)
        row = {
            "slug": slug,
            "name": _candidate_name(candidate),
            "repo": _candidate_repo_full_name(candidate, repo),
            "score": adjusted_total,
            "weighted_score": adjusted_total,
            "score_breakdown": breakdown,
            "reasons": _top_reasons(breakdown),
            "selection_state": _value(candidate, "selection_state", "included"),
            "is_locked": bool(_value(candidate, "is_locked", False)),
        }
        if has_phase3_risks:
            row.update(
                {
                    "base_weighted_score": weighted_total,
                    "risk_adjustment": risk_adjustment,
                    "confirmed_risks": risk_summary["confirmed_risks"],
                    "unresolved_risks": risk_summary["unresolved_risks"],
                    "contradicted_risks": risk_summary["contradicted_risks"],
                }
            )
        ranked.append(row)

    ranked.sort(key=lambda item: item["weighted_score"], reverse=True)
    winner = ranked[0]
    if has_phase3_risks:
        summary = (
            f"Recommended v{version_number}: {winner['name']}. It scored "
            f"{winner['weighted_score']}/100 after Phase 3 risk-adjusted scoring."
        )
    else:
        summary = (
            f"Recommended v{version_number}: {winner['name']}. It scored "
            f"{winner['weighted_score']}/100 using selected Phase 2 criteria and weights."
        )
    rationale = {
        "recommended_slug": winner["slug"],
        "recommended_repo": winner["repo"],
        "recommended_version": version_number,
        "ranked_candidates": ranked,
    }
    return summary, rationale


def score_candidate_for_criterion(
    candidate: Any,
    criterion: CriterionProfile,
    repo: RepositorySummary | None,
    context: dict,
) -> dict:
    capabilities = set(_candidate_capabilities(candidate))
    constraints = set(context.get("constraints", []))
    name = criterion.name
    score = 50
    explanation = "No strong deterministic fit signal was found."

    if name == "Runtime control and state":
        if {"checkpointing", "stateful_graph"} & capabilities:
            score = 100
            explanation = "Strong runtime state and checkpointing fit."
        elif {"handoffs", "tool_calling"} & capabilities:
            score = 65
            explanation = "Partial runtime fit through handoffs or tool calls."
    elif name == "Human-in-the-loop fit":
        if "human_in_loop" in capabilities:
            score = 100
            explanation = "Explicit human-in-the-loop capability signal."
        elif {"guardrails", "handoffs"} & capabilities:
            score = 85
            explanation = "Guardrails or handoffs provide review-point fit."
        else:
            score = 40
            explanation = "No explicit approval or handoff signal."
    elif name == "Observability and traceability":
        if {"tracing", "guardrails"} & capabilities:
            score = 100
            explanation = "Tracing or guardrail capability supports auditability."
        elif "observability" in constraints:
            score = 60
            explanation = "Observability is required, but only indirect signals exist."
        else:
            score = 50
            explanation = "Traceability support needs more evidence."
    elif name == "Developer ergonomics":
        if "python" in capabilities:
            score = 85
            explanation = "Python support fits the current backend stack."
        elif "custom" in capabilities:
            score = 55
            explanation = "Custom candidate ergonomics need follow-up validation."
    elif name == "Open-source project health":
        score, explanation = _project_health_score(repo)

    return {
        "status": _status_for_score(score),
        "score": score,
        "weight": criterion.weight,
        "explanation": explanation,
    }


def _repo_for_candidate(
    candidate: Any,
    repos: dict[str, RepositorySummary],
) -> RepositorySummary | None:
    slug = _candidate_slug(candidate)
    repo_full_name = _candidate_repo_full_name(candidate, None)
    return repos.get(slug) or repos.get(repo_full_name)


def _project_health_score(repo: RepositorySummary | None) -> tuple[int, str]:
    if repo is None:
        return 35, "Repository health evidence is missing."
    if repo.stars >= 20000:
        return 100, "GitHub metadata shows strong adoption."
    if repo.stars >= 10000:
        return 85, "GitHub metadata shows solid adoption."
    if repo.stars >= 1000:
        return 70, "GitHub metadata shows moderate adoption."
    return 45, "GitHub adoption signal is limited."


def _status_for_score(score: int) -> str:
    if score >= 85:
        return "strong"
    if score >= 60:
        return "partial"
    if score > 0:
        return "weak"
    return "unknown"


def _top_reasons(breakdown: dict[str, dict]) -> list[str]:
    strongest = sorted(
        breakdown.items(),
        key=lambda item: (item[1]["score"], item[1]["weight"]),
        reverse=True,
    )
    return [f"{name}: {payload['explanation']}" for name, payload in strongest[:2]]


def _has_phase3_risk_context(context: Mapping[str, Any]) -> bool:
    return bool(context.get("phase3_risk_adjustments") or context.get("phase3_risk_signals"))


def _phase3_risk_summary(slug: str, context: Mapping[str, Any]) -> dict:
    risk_adjustments = context.get("phase3_risk_adjustments")
    adjustment_payload = (
        risk_adjustments.get(slug, {})
        if isinstance(risk_adjustments, Mapping)
        else {}
    )
    risks = [
        risk
        for risk in _list_value(context.get("phase3_risk_signals"))
        if isinstance(risk, Mapping) and _normalize_slug(risk.get("candidate_slug")) == slug
    ]
    return {
        "risk_adjustment": int(adjustment_payload.get("risk_adjustment") or 0),
        "confirmed_risks": _risk_titles(risks, "confirmed"),
        "unresolved_risks": _risk_titles(risks, "unresolved"),
        "contradicted_risks": _risk_titles(risks, "contradicted"),
    }


def _risk_titles(risks: list[Mapping[str, Any]], status: str) -> list[str]:
    return [
        str(risk.get("title") or risk.get("risk_key") or "Risk").strip()
        for risk in risks
        if risk.get("status") == status
    ]


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _candidate_capabilities(candidate: Any) -> tuple[str, ...]:
    capabilities = _value(candidate, "capabilities", ())
    if isinstance(capabilities, tuple) and capabilities:
        return capabilities
    if isinstance(capabilities, list) and capabilities:
        return tuple(str(capability) for capability in capabilities)
    registry_candidate = registry_by_slug().get(_candidate_slug(candidate))
    if registry_candidate is not None:
        return registry_candidate.capabilities
    return ()


def _candidate_slug(candidate: Any) -> str:
    return str(_value(candidate, "slug")).strip()


def _normalize_slug(value: Any) -> str:
    return str(value or "").strip().lower()


def _candidate_name(candidate: Any) -> str:
    return str(_value(candidate, "name")).strip()


def _candidate_repo_full_name(
    candidate: Any,
    repo: RepositorySummary | None,
) -> str:
    if repo is not None:
        return repo.full_name
    return str(_value(candidate, "repo_full_name")).strip()


def _value(item: Any, key: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)
