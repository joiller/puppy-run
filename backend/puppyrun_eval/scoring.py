"""Quality scoring helpers for Phase 4 eval cases."""

from __future__ import annotations

from typing import Any

from puppyrun_agent.llm_providers import STRONG_SOURCE_TYPES, normalize_strong_source_type

VALID_RISK_STATUSES = {"confirmed", "contradicted", "unresolved", "unverified"}


def assert_non_empty(value: object, *, field_name: str) -> None:
    if not value:
        raise AssertionError(f"Expected non-empty {field_name}.")


def assert_claim_contract(claims: list[Any]) -> None:
    assert_non_empty(claims, field_name="claims")
    first_claim = claims[0]
    if not str(first_claim.title).strip():
        raise AssertionError("Expected extracted claim title to be non-empty.")
    candidate_slugs = {str(claim.candidate_slug) for claim in claims}
    source_types = {str(claim.source_type) for claim in claims}
    if "langgraph" not in candidate_slugs:
        raise AssertionError("Expected claim extraction to preserve candidate slug.")
    if not {"official_docs", "github_release"} & source_types:
        raise AssertionError("Expected claim extraction to preserve strong source type.")
    if "hacker_news" not in source_types:
        raise AssertionError("Expected claim extraction to preserve community source type.")


def assert_low_trust_risk_contract(risks: list[Any]) -> None:
    assert_non_empty(risks, field_name="risks")
    for risk in risks:
        if not str(risk.title).strip():
            raise AssertionError("Expected risk cluster title to be non-empty.")
        if risk.status == "confirmed":
            raise AssertionError("Expected community-only low-trust risk to remain unconfirmed.")


def assert_verification_contract(
    *,
    tasks: list[Any],
    verdict: Any,
    synthesis: Any,
) -> None:
    assert_non_empty(tasks, field_name="verification tasks")
    raw_task_source_types = {str(task.stronger_source_type) for task in tasks}
    task_source_types = {normalize_strong_source_type(source) for source in raw_task_source_types}
    if not task_source_types & STRONG_SOURCE_TYPES:
        observed = ", ".join(sorted(raw_task_source_types)) or "none"
        raise AssertionError(
            f"Expected verification task to target a stronger source type; saw {observed}."
        )

    if verdict.verdict not in VALID_RISK_STATUSES:
        raise AssertionError("Expected verification verdict to be a valid risk status.")
    verdict_source_type = normalize_strong_source_type(verdict.source_type)
    if verdict.verdict == "confirmed" and verdict_source_type not in STRONG_SOURCE_TYPES:
        raise AssertionError("Expected confirmed verdict to be backed by a stronger source.")

    if not isinstance(synthesis.summary, str):
        raise AssertionError("Expected risk synthesis summary to be safe text.")
    for field_name in (
        "confirmed_risks",
        "unresolved_risks",
        "contradicted_risks",
        "unverified_risks",
    ):
        if not isinstance(getattr(synthesis, field_name), list):
            raise AssertionError(f"Expected risk synthesis {field_name} to be a list.")


def assert_workflow_regression_contract(workspace: Any) -> dict[str, object]:
    session = workspace.session
    active_version = workspace.active_version
    if active_version is None:
        raise AssertionError("Expected workflow to produce an active decision version.")
    if getattr(session.status, "value", session.status) != "completed":
        raise AssertionError("Expected workflow session to be completed.")
    if getattr(active_version.status, "value", active_version.status) != "completed":
        raise AssertionError("Expected workflow version to be completed.")

    if len(workspace.candidates) < 3:
        raise AssertionError("Expected workflow workspace to have at least 3 candidates.")
    if len(workspace.criteria) < 5:
        raise AssertionError("Expected workflow workspace to have at least 5 criteria.")

    evidence_source_types = {str(item.source_type) for item in workspace.evidence_items}
    if "github_repo" not in evidence_source_types:
        raise AssertionError("Expected GitHub repository evidence.")
    if not any(_is_phase3_evidence(item) for item in workspace.evidence_items):
        raise AssertionError("Expected Phase 3 source evidence.")

    if not workspace.claims:
        raise AssertionError("Expected Phase 3 claims.")
    if not workspace.risk_signals:
        raise AssertionError("Expected Phase 3 risk signals.")
    if not workspace.verification_tasks:
        raise AssertionError("Expected Phase 3 verification tasks.")
    if not workspace.tool_calls:
        raise AssertionError("Expected workflow tool calls.")

    gap_analysis = dict(getattr(active_version, "gap_analysis", None) or {})
    if "phase3_failure" in gap_analysis:
        raise AssertionError("Expected provider-backed Phase 3 output, not phase3_failure.")
    risk_adjusted_scores = gap_analysis.get("risk_adjusted_scores")
    if not isinstance(risk_adjusted_scores, dict) or not risk_adjusted_scores:
        raise AssertionError("Expected Phase 3 risk-adjusted score facts.")
    risk_adjusted_slugs = {
        str(slug)
        for slug, payload in risk_adjusted_scores.items()
        if isinstance(payload, dict) and "risk_adjustment" in payload
    }
    if not risk_adjusted_slugs:
        raise AssertionError("Expected risk adjustment facts for workflow candidates.")

    if not workspace.recommendations:
        raise AssertionError("Expected recommendation output.")
    recommendation = workspace.recommendations[-1]
    winner = _candidate_by_id(workspace.candidates, recommendation.recommended_candidate_id)
    if winner is None:
        raise AssertionError("Expected recommendation to reference a candidate.")
    summary = str(recommendation.summary or "")
    if winner.name not in summary:
        raise AssertionError("Expected recommendation summary to reference the winning candidate.")
    rationale = dict(recommendation.rationale or {})
    ranked = rationale.get("ranked_candidates")
    if not isinstance(ranked, list) or not ranked:
        raise AssertionError("Expected recommendation ranked candidate facts.")
    winner_row = next(
        (row for row in ranked if isinstance(row, dict) and row.get("slug") == winner.slug),
        None,
    )
    if winner_row is None:
        raise AssertionError("Expected recommendation rationale to include the winning candidate.")
    if "risk_adjustment" not in winner_row:
        raise AssertionError("Expected recommendation rationale to include risk adjustment facts.")

    adr = str(active_version.adr or "")
    if not _adr_has_risk_reasoning(
        adr,
        risk_signals=workspace.risk_signals,
        candidates=workspace.candidates,
        risk_adjusted_slugs=risk_adjusted_slugs,
    ):
        raise AssertionError("Expected ADR to include risk reasoning.")

    return {
        "candidate_count": len(workspace.candidates),
        "criterion_count": len(workspace.criteria),
        "evidence_source_types": sorted(evidence_source_types),
        "claim_count": len(workspace.claims),
        "risk_signal_count": len(workspace.risk_signals),
        "verification_task_count": len(workspace.verification_tasks),
        "tool_call_count": len(workspace.tool_calls),
        "recommended_slug": winner.slug,
        "risk_adjusted_slugs": sorted(risk_adjusted_scores),
    }


def _is_phase3_evidence(item: Any) -> bool:
    payload = getattr(item, "payload", None)
    return isinstance(payload, dict) and payload.get("phase3") is True


def _candidate_by_id(candidates: list[Any], candidate_id: Any) -> Any | None:
    for candidate in candidates:
        if str(candidate.id) == str(candidate_id):
            return candidate
    return None


def _adr_has_risk_reasoning(
    adr: str,
    *,
    risk_signals: list[Any],
    candidates: list[Any],
    risk_adjusted_slugs: set[str],
) -> bool:
    adr_text = adr.lower()
    if "risk" not in adr_text:
        return False

    risk_titles = {
        str(getattr(risk, "title", "") or "").strip().lower()
        for risk in risk_signals
        if str(getattr(risk, "title", "") or "").strip()
    }
    if any(title in adr_text for title in risk_titles):
        return True

    candidate_names = {
        str(getattr(candidate, "name", "") or "").strip().lower()
        for candidate in candidates
        if str(getattr(candidate, "slug", "") or "") in risk_adjusted_slugs
    }
    candidate_slugs = {slug.lower() for slug in risk_adjusted_slugs}
    has_adjusted_candidate = any(
        label and label in adr_text
        for label in candidate_names | candidate_slugs
    )
    has_score_impact = "score" in adr_text or "adjust" in adr_text or "impact" in adr_text
    return has_adjusted_candidate and has_score_impact
