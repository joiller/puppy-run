from collections.abc import Iterable, Mapping
from typing import Any

from puppyrun_agent.catalog import (
    CandidateProfile,
    custom_candidate_from_draft,
    registry_by_slug,
    select_candidates,
)
from puppyrun_agent.criteria import CriterionProfile, apply_weight_overrides
from puppyrun_agent.github_client import RepositorySummary
from puppyrun_agent.recommendation import score_candidate_for_criterion

EMPTY_DRAFT: dict[str, Any] = {
    "source_version_id": None,
    "candidate_overrides": {},
    "custom_candidates": {},
    "must_include_constraints": {},
    "must_exclude_constraints": {},
    "weight_overrides": {},
}


def normalize_phase2_draft(raw: Mapping[str, Any] | None, source_version_id: Any) -> dict:
    raw = raw if isinstance(raw, Mapping) else {}
    resolved_source_version_id = raw.get("source_version_id") or source_version_id
    return {
        "source_version_id": (
            str(resolved_source_version_id)
            if resolved_source_version_id is not None
            else None
        ),
        "candidate_overrides": _normalize_candidate_overrides(
            raw.get("candidate_overrides")
        ),
        "custom_candidates": _normalize_custom_candidates(raw.get("custom_candidates")),
        "must_include_constraints": _normalize_constraint_overrides(
            raw.get("must_include_constraints")
        ),
        "must_exclude_constraints": _normalize_constraint_overrides(
            raw.get("must_exclude_constraints")
        ),
        "weight_overrides": _normalize_weight_overrides(raw.get("weight_overrides")),
    }


def apply_phase2_constraints(context: Mapping[str, Any], draft: Mapping[str, Any]) -> dict:
    effective = dict(context)
    constraints = {
        _normalize_slug(constraint)
        for constraint in _as_list(context.get("constraints"))
        if _clean_text(constraint)
    }
    includes = _enabled_override_keys(draft.get("must_include_constraints"))
    excludes = _enabled_override_keys(draft.get("must_exclude_constraints"))

    constraints.update(includes)
    constraints.difference_update(excludes)
    effective["constraints"] = sorted(constraints)
    effective["phase2_must_include_constraints"] = sorted(includes)
    effective["phase2_must_exclude_constraints"] = sorted(excludes)
    return effective


def build_phase2_candidates(context: Mapping[str, Any], draft: Mapping[str, Any]) -> list[dict]:
    candidates = [_candidate_to_dict(candidate) for candidate in _baseline_candidates(context)]
    candidates_by_slug = {candidate["slug"]: candidate for candidate in candidates}
    candidate_order = [candidate["slug"] for candidate in candidates]

    for slug, override in _as_mapping(draft.get("candidate_overrides")).items():
        normalized_slug = _normalize_slug(slug)
        action = _clean_text(_value(override, "action")).lower()
        if action in {"exclude", "must_exclude"}:
            candidates_by_slug.pop(normalized_slug, None)
            candidate_order = [
                existing_slug
                for existing_slug in candidate_order
                if existing_slug != normalized_slug
            ]
            continue

        if normalized_slug not in candidates_by_slug:
            registry_candidate = registry_by_slug().get(normalized_slug)
            if registry_candidate is not None:
                candidates_by_slug[normalized_slug] = _candidate_to_dict(registry_candidate)
                candidate_order.append(normalized_slug)

        if normalized_slug in candidates_by_slug:
            candidates_by_slug[normalized_slug] = _apply_candidate_override(
                candidates_by_slug[normalized_slug],
                override,
            )

    for slug, custom_candidate in sorted(
        _as_mapping(draft.get("custom_candidates")).items()
    ):
        candidate = _custom_candidate_to_dict(slug, custom_candidate)
        if candidate["slug"] not in candidates_by_slug:
            candidate_order.append(candidate["slug"])
        candidates_by_slug[candidate["slug"]] = candidate

    return [
        candidates_by_slug[slug]
        for slug in candidate_order
        if slug in candidates_by_slug
    ]


def apply_phase2_criteria(
    criteria: Iterable[Any],
    draft: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[dict]:
    _ = context
    adjusted = apply_weight_overrides(
        [_criterion_profile(criterion) for criterion in criteria],
        dict(_as_mapping(draft.get("weight_overrides"))),
    )
    return [_criterion_to_dict(criterion) for criterion in adjusted]


def build_research_plan(
    candidates: Iterable[Any],
    previous_evidence: Iterable[Any],
) -> dict:
    evidence_keys = _github_evidence_keys(previous_evidence)
    research_tasks = []
    reuse_tasks = []
    for candidate in candidates:
        candidate_slug = _normalize_slug(_value(candidate, "slug"))
        repo_full_name = _clean_text(_value(candidate, "repo_full_name"))
        task = {
            "candidate_slug": candidate_slug,
            "repo_full_name": repo_full_name,
        }
        if (candidate_slug in evidence_keys["slugs"]) or (
            repo_full_name in evidence_keys["repos"]
        ):
            reuse_tasks.append({**task, "reason": "existing_github_evidence"})
        else:
            research_tasks.append({**task, "reason": "missing_github_evidence"})
    return {
        "research_tasks": research_tasks,
        "reuse_tasks": reuse_tasks,
    }


def build_gap_analysis(
    draft: Mapping[str, Any],
    candidates: Iterable[Any],
    criteria: Iterable[Any],
    previous_evidence: Iterable[Any],
) -> dict:
    _ = criteria
    changed_candidates = sorted(
        {
            *(_normalize_slug(slug) for slug in _as_mapping(draft.get("custom_candidates"))),
            *(
                _normalize_slug(slug)
                for slug in _as_mapping(draft.get("candidate_overrides"))
            ),
        }
    )
    changed_constraints = sorted(
        {
            *_enabled_override_keys(draft.get("must_include_constraints")),
            *_enabled_override_keys(draft.get("must_exclude_constraints")),
        }
    )
    changed_weights = sorted(_as_mapping(draft.get("weight_overrides")).keys())
    research_plan = build_research_plan(candidates, previous_evidence)
    requires_research = bool(research_plan["research_tasks"])
    score_only = (
        bool(changed_weights)
        and not changed_candidates
        and not changed_constraints
        and not requires_research
    )
    return {
        "requires_research": requires_research,
        "requires_github_fetch": requires_research,
        "score_only": score_only,
        "changed_candidates": changed_candidates,
        "changed_constraints": changed_constraints,
        "changed_weights": changed_weights,
        "research_tasks": research_plan["research_tasks"],
        "reuse_tasks": research_plan["reuse_tasks"],
        "items": _gap_items(
            changed_candidates,
            changed_constraints,
            changed_weights,
            research_plan["research_tasks"],
        ),
    }


def build_score_cells(
    candidates: Iterable[Any],
    criteria: Iterable[Any],
    repos: Mapping[str, RepositorySummary],
    evidence_by_candidate: Mapping[str, list[dict]],
) -> list[dict]:
    cells = []
    for candidate in candidates:
        scoring_candidate = _scoring_candidate(candidate)
        candidate_slug = _normalize_slug(_value(scoring_candidate, "slug"))
        repo = _repo_for_candidate(scoring_candidate, repos)
        evidence_refs = _evidence_refs(candidate_slug, repo, evidence_by_candidate)
        for criterion in criteria:
            criterion_profile = _criterion_profile(criterion)
            score = score_candidate_for_criterion(
                scoring_candidate,
                criterion_profile,
                repo,
                {},
            )
            cells.append(
                {
                    "candidate_slug": candidate_slug,
                    "criterion_name": criterion_profile.name,
                    "status": score["status"],
                    "score": score["score"],
                    "explanation": score["explanation"],
                    "evidence_refs": evidence_refs,
                }
            )
    return cells


def build_adr(
    version_number: int,
    summary: str,
    rationale: Mapping[str, Any],
    gap_analysis: Mapping[str, Any],
    score_cells: Iterable[Mapping[str, Any]],
) -> dict:
    title = f"ADR v{version_number}: {summary}"
    ranked_candidates = _as_list(rationale.get("ranked_candidates"))
    evidence_links = _adr_evidence_links(score_cells)
    body = "\n\n".join(
        [
            "## Context\n"
            f"Version {version_number} reflects Phase 2 draft changes. "
            f"Changed candidates: {_join_or_none(gap_analysis.get('changed_candidates'))}. "
            f"Changed constraints: {_join_or_none(gap_analysis.get('changed_constraints'))}. "
            f"Changed weights: {_join_or_none(gap_analysis.get('changed_weights'))}.",
            f"## Decision\n{summary}",
            "## Options\n" + _adr_options(ranked_candidates),
            "## Rationale\n" + _adr_rationale(ranked_candidates),
            "## Tradeoffs\n"
            "The decision favors the weighted Phase 2 criteria while preserving the "
            "ranked alternatives for review.",
            "## Risks\n" + _adr_risks(gap_analysis),
            "## Evidence links\n" + _adr_evidence_body(evidence_links),
        ]
    )
    return {"title": title, "body": body}


def _normalize_candidate_overrides(raw: Any) -> dict:
    normalized = {}
    for slug, payload in _as_mapping(raw).items():
        normalized_slug = _normalize_slug(slug)
        normalized[normalized_slug] = {
            "action": _clean_text(_value(payload, "action")).lower(),
            "reason": _clean_text(_value(payload, "reason")),
        }
    return normalized


def _normalize_custom_candidates(raw: Any) -> dict:
    normalized = {}
    for slug, payload in _as_mapping(raw).items():
        normalized_slug = _normalize_slug(_value(payload, "slug") or slug)
        normalized[normalized_slug] = {
            "name": _clean_text(_value(payload, "name")),
            "slug": normalized_slug,
            "repo_full_name": _clean_text(_value(payload, "repo_full_name")),
            "reason": _clean_text(_value(payload, "reason")),
        }
    return normalized


def _normalize_constraint_overrides(raw: Any) -> dict:
    normalized = {}
    for slug, payload in _as_mapping(raw).items():
        normalized_slug = _normalize_slug(slug)
        normalized[normalized_slug] = {
            "enabled": bool(_value(payload, "enabled", True)),
            "reason": _clean_text(_value(payload, "reason")),
        }
    return normalized


def _normalize_weight_overrides(raw: Any) -> dict:
    normalized = {}
    for criterion_name, payload in _as_mapping(raw).items():
        normalized_name = _clean_text(criterion_name)
        normalized[normalized_name] = {
            "weight": int(_value(payload, "weight")),
            "reason": _clean_text(_value(payload, "reason")),
        }
    return normalized


def _enabled_override_keys(raw: Any) -> set[str]:
    return {
        _normalize_slug(slug)
        for slug, payload in _as_mapping(raw).items()
        if bool(_value(payload, "enabled", True))
    }


def _baseline_candidates(context: Mapping[str, Any]) -> list[Any]:
    explicit_candidates = context.get("candidates")
    if isinstance(explicit_candidates, Iterable) and not isinstance(
        explicit_candidates, (str, bytes, Mapping)
    ):
        return list(explicit_candidates)

    mentioned = [_normalize_slug(slug) for slug in _as_list(context.get("mentioned_candidates"))]
    if mentioned:
        registry = registry_by_slug()
        return [registry[slug] for slug in mentioned if slug in registry]

    return list(select_candidates(dict(context)))


def _candidate_to_dict(candidate: Any) -> dict:
    if isinstance(candidate, CandidateProfile):
        return {
            "name": candidate.name,
            "slug": candidate.slug,
            "repo_full_name": candidate.repo_full_name,
            "include_reason": candidate.include_reason,
            "selection_state": "included",
            "is_locked": False,
        }
    return {
        "name": _clean_text(_value(candidate, "name")),
        "slug": _normalize_slug(_value(candidate, "slug")),
        "repo_full_name": _clean_text(_value(candidate, "repo_full_name")),
        "include_reason": _clean_text(_value(candidate, "include_reason")),
        "selection_state": _clean_text(_value(candidate, "selection_state", "included")),
        "is_locked": bool(_value(candidate, "is_locked", False)),
    }


def _custom_candidate_to_dict(slug: str, payload: Any) -> dict:
    candidate = custom_candidate_from_draft(slug, dict(_as_mapping(payload)))
    return {
        "name": candidate.name,
        "slug": candidate.slug,
        "repo_full_name": candidate.repo_full_name,
        "capabilities": candidate.capabilities,
        "include_reason": candidate.include_reason,
        "selection_state": "included",
        "is_locked": False,
        "is_custom": True,
    }


def _apply_candidate_override(candidate: dict, override: Any) -> dict:
    adjusted = dict(candidate)
    action = _clean_text(_value(override, "action")).lower()
    if action == "lock":
        adjusted["selection_state"] = "locked"
        adjusted["is_locked"] = True
    elif action in {"include", "must_include"}:
        adjusted["selection_state"] = "included"
    reason = _clean_text(_value(override, "reason"))
    if reason:
        adjusted["phase2_override_reason"] = reason
    return adjusted


def _criterion_to_dict(criterion: Any) -> dict:
    return {
        "name": _clean_text(_value(criterion, "name")),
        "weight": int(_value(criterion, "weight")),
        "rationale": _clean_text(_value(criterion, "rationale")),
        "evidence_needed": _clean_text(_value(criterion, "evidence_needed")),
        "is_locked": bool(_value(criterion, "is_locked", False)),
        "phase2_weight_reason": _clean_text(_value(criterion, "phase2_weight_reason")),
    }


def _criterion_profile(criterion: Any) -> CriterionProfile:
    return CriterionProfile(
        name=_clean_text(_value(criterion, "name")),
        weight=int(_value(criterion, "weight")),
        rationale=_clean_text(_value(criterion, "rationale")),
        evidence_needed=_clean_text(_value(criterion, "evidence_needed")),
        is_locked=bool(_value(criterion, "is_locked", False)),
        phase2_weight_reason=_clean_text(_value(criterion, "phase2_weight_reason")),
    )


def _scoring_candidate(candidate: Any) -> Any:
    capabilities = _value(candidate, "capabilities", None)
    if capabilities:
        return candidate
    return registry_by_slug().get(_normalize_slug(_value(candidate, "slug")), candidate)


def _repo_for_candidate(
    candidate: Any,
    repos: Mapping[str, RepositorySummary],
) -> RepositorySummary | None:
    candidate_slug = _normalize_slug(_value(candidate, "slug"))
    repo_full_name = _clean_text(_value(candidate, "repo_full_name"))
    return repos.get(candidate_slug) or repos.get(repo_full_name)


def _evidence_refs(
    candidate_slug: str,
    repo: RepositorySummary | None,
    evidence_by_candidate: Mapping[str, list[dict]],
) -> list[dict]:
    refs = [dict(ref) for ref in evidence_by_candidate.get(candidate_slug, [])]
    if not refs and repo is not None:
        refs.append(
            {
                "source_type": "github_repo",
                "label": repo.full_name,
                "source_url": repo.source_url,
            }
        )
    return refs


def _adr_options(ranked_candidates: list[Any]) -> str:
    if not ranked_candidates:
        return "- No ranked candidates were produced."
    return "\n".join(
        f"- {_clean_text(_value(candidate, 'name')) or _value(candidate, 'slug')}: "
        f"{_value(candidate, 'weighted_score', _value(candidate, 'score', 0))}/100"
        for candidate in ranked_candidates
    )


def _adr_rationale(ranked_candidates: list[Any]) -> str:
    if not ranked_candidates:
        return "No rationale was produced."
    winner = ranked_candidates[0]
    reasons = _as_list(_value(winner, "reasons"))
    if not reasons:
        return f"{_value(winner, 'name')} ranked first under the selected criteria."
    return "\n".join(f"- {reason}" for reason in reasons)


def _adr_risks(gap_analysis: Mapping[str, Any]) -> str:
    research_tasks = _as_list(gap_analysis.get("research_tasks"))
    if not research_tasks:
        return "- No unresolved research tasks were identified by gap analysis."
    return "\n".join(
        f"- {task.get('candidate_slug')}: {task.get('reason')} for "
        f"{task.get('repo_full_name')}"
        for task in research_tasks
        if isinstance(task, Mapping)
    )


def _adr_evidence_links(score_cells: Iterable[Mapping[str, Any]]) -> list[dict]:
    links_by_url = {}
    for cell in score_cells:
        for ref in _as_list(cell.get("evidence_refs")):
            if not isinstance(ref, Mapping):
                continue
            source_url = _clean_text(ref.get("source_url"))
            if source_url:
                links_by_url[source_url] = {
                    "label": _clean_text(ref.get("label")) or source_url,
                    "source_url": source_url,
                }
    return list(links_by_url.values())


def _adr_evidence_body(evidence_links: list[dict]) -> str:
    if not evidence_links:
        return "- No evidence links were attached."
    return "\n".join(
        f"- [{link['label']}]({link['source_url']})" for link in evidence_links
    )


def _join_or_none(value: Any) -> str:
    items = [str(item) for item in _as_list(value)]
    return ", ".join(items) if items else "none"


def _github_evidence_keys(previous_evidence: Iterable[Any]) -> dict[str, set[str]]:
    slugs = set()
    repos = set()
    for evidence in previous_evidence:
        if _clean_text(_value(evidence, "source_type")) != "github_repo":
            continue
        candidate_slug = _clean_text(_value(evidence, "candidate_slug"))
        repo_full_name = _clean_text(_value(evidence, "repo_full_name"))
        if candidate_slug:
            slugs.add(_normalize_slug(candidate_slug))
        if repo_full_name:
            repos.add(repo_full_name)
    return {"slugs": slugs, "repos": repos}


def _gap_items(
    changed_candidates: list[str],
    changed_constraints: list[str],
    changed_weights: list[str],
    research_tasks: list[dict],
) -> list[dict]:
    items = []
    items.extend(
        {"kind": "candidate_change", "candidate_slug": slug}
        for slug in changed_candidates
    )
    items.extend(
        {"kind": "constraint_change", "constraint": constraint}
        for constraint in changed_constraints
    )
    items.extend({"kind": "weight_change", "label": weight} for weight in changed_weights)
    items.extend({"kind": "research_required", **task} for task in research_tasks)
    if not items:
        items.append({"kind": "no_change", "message": "Draft matches the source baseline."})
    return items


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _value(item: Any, key: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_slug(value: Any) -> str:
    return _clean_text(value).lower()
