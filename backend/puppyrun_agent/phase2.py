from collections.abc import Iterable, Mapping
from typing import Any

from puppyrun_agent.catalog import CANDIDATE_REGISTRY, CandidateProfile, select_candidates

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
            registry_candidate = _registry_by_slug().get(normalized_slug)
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
    overrides = _as_mapping(draft.get("weight_overrides"))
    adjusted = []
    for criterion in criteria:
        criterion_dict = _criterion_to_dict(criterion)
        override = overrides.get(criterion_dict["name"])
        if isinstance(override, Mapping):
            criterion_dict["weight"] = int(override.get("weight", criterion_dict["weight"]))
            criterion_dict["is_locked"] = True
            criterion_dict["phase2_weight_reason"] = _clean_text(override.get("reason"))
        adjusted.append(criterion_dict)
    return adjusted


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
        registry = _registry_by_slug()
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
    normalized_slug = _normalize_slug(_value(payload, "slug") or slug)
    reason = _clean_text(_value(payload, "reason"))
    return {
        "name": _clean_text(_value(payload, "name")),
        "slug": normalized_slug,
        "repo_full_name": _clean_text(_value(payload, "repo_full_name")),
        "include_reason": reason,
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
    }


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


def _registry_by_slug() -> dict[str, CandidateProfile]:
    return {candidate.slug: candidate for candidate in CANDIDATE_REGISTRY}


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
