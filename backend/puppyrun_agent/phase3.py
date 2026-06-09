import re
from collections.abc import Iterable, Mapping
from typing import Any

from puppyrun_agent.llm_providers import (
    COMMUNITY_SOURCE_TYPES,
    STRONG_SOURCE_TYPES,
    DeterministicLLMProvider,
    ExtractedClaim,
    RiskCluster,
)
from puppyrun_agent.tool_runtime import content_hash, hashable_payload

Credibility = str

HIGH_CREDIBILITY_SOURCE_TYPES = {
    "official_docs",
    "source_code",
    "official_release",
    "github_release",
}
MEDIUM_CREDIBILITY_SOURCE_TYPES = {
    "github_issue",
    "github_pr",
    "technical_blog",
    "arxiv",
    "paper",
    "benchmark",
}
LOW_CREDIBILITY_SOURCE_TYPES = {
    *COMMUNITY_SOURCE_TYPES,
    "stackoverflow",
    "community_discussion",
}
STRONG_VERIFICATION_SOURCE_TYPES = {
    *STRONG_SOURCE_TYPES,
    "source_code",
    "official_release",
    "github_pr",
    "paper",
    "benchmark",
}
PROVIDER_STRONG_SOURCE_TYPE_ALIASES = {
    "source_code": "official_docs",
    "official_release": "github_release",
    "github_pr": "github_issue",
    "paper": "arxiv",
    "benchmark": "technical_blog",
}
RISK_IMPACT_BY_SEVERITY = {
    "low": -2,
    "medium": -5,
    "high": -8,
}
MAX_CANDIDATE_RISK_ADJUSTMENT = -15
MAX_EVIDENCE_TEXT_LENGTH = 500


def normalize_evidence_items(evidence_items: Iterable[Any]) -> list[dict]:
    normalized = []
    for item in evidence_items:
        payload = _as_mapping(_value(item, "payload"))
        metadata = _as_mapping(_value(item, "metadata")) or payload
        source_type = _clean(_value(item, "source_type"))
        source_url = _clean(_value(item, "source_url"))
        title = _shorten(_clean(_value(item, "title")), limit=200) or source_type
        summary = _shorten(_clean(_value(item, "summary")))
        citation_text = _shorten(
            _clean(_value(item, "citation_text")) or summary or title,
        )
        candidate_slug = (
            _clean(_value(item, "candidate_slug"))
            or _clean(payload.get("candidate_slug"))
            or _clean(metadata.get("candidate_slug"))
        )
        row = {
            "candidate_slug": candidate_slug,
            "source_type": source_type,
            "source_url": source_url,
            "title": title,
            "summary": summary or title,
            "citation_text": citation_text,
            "credibility": credibility_for_source_type(
                source_type,
                explicit=_clean(_value(item, "credibility")),
            ),
            "metadata": hashable_payload(_drop_raw_content(metadata)),
        }
        row["content_hash"] = _clean(_value(item, "content_hash")) or content_hash(
            {
                "source_type": row["source_type"],
                "source_url": row["source_url"],
                "title": row["title"],
                "summary": row["summary"],
                "citation_text": row["citation_text"],
            }
        )
        normalized.append(row)
    return normalized


def credibility_for_source_type(source_type: str, *, explicit: str = "") -> Credibility:
    normalized = source_type.strip().lower()
    if normalized in HIGH_CREDIBILITY_SOURCE_TYPES:
        return "high"
    if normalized in MEDIUM_CREDIBILITY_SOURCE_TYPES:
        return "medium"
    if normalized in LOW_CREDIBILITY_SOURCE_TYPES:
        return "low"
    explicit = explicit.strip().lower()
    if explicit in {"high", "medium", "low"}:
        return explicit
    return "medium"


def build_risk_verification_pipeline(
    evidence_items: Iterable[Any],
    *,
    provider: Any | None = None,
) -> dict:
    normalized_evidence = normalize_evidence_items(evidence_items)
    llm_provider = provider or DeterministicLLMProvider()
    extracted = llm_provider.extract_claims(normalized_evidence)
    claim_models = list(extracted.claims)
    claims = _claim_dicts(claim_models, normalized_evidence)
    clustered = llm_provider.cluster_risks(claim_models)
    grouped_risks = _group_risks(clustered.risks, claim_models)
    planned_tasks = {
        (task.candidate_slug, _normalize_risk_key(task.risk_key)): task
        for task in llm_provider.plan_verification(grouped_risks).tasks
    }

    risk_signals = []
    verification_tasks = []
    for risk in grouped_risks:
        stronger_evidence = _stronger_evidence_for_risk(
            risk,
            claim_models,
            normalized_evidence,
        )
        verdict = None
        status = risk.status
        if stronger_evidence:
            verdict = llm_provider.verify_risk(risk, stronger_evidence=stronger_evidence)
            status = verdict.verdict
        elif status != "unverified":
            status = "unresolved"

        score_impact = score_impact_for_risk(status=status, severity=risk.severity)
        task = planned_tasks.get((risk.candidate_slug, _normalize_risk_key(risk.risk_key)))
        stronger_source = stronger_evidence[0] if stronger_evidence else {}
        verification_task = {
            "candidate_slug": risk.candidate_slug,
            "risk_key": risk.risk_key,
            "status": "completed" if verdict else "planned",
            "verification_question": (
                task.verification_question
                if task is not None
                else f"Find stronger evidence for {risk.candidate_slug} {risk.title}."
            ),
            "stronger_source_type": (
                _clean(stronger_source.get("original_source_type"))
                or _clean(stronger_source.get("source_type"))
                or (task.stronger_source_type if task is not None else "official_docs")
            ),
            "stronger_source_url": (
                _clean(stronger_source.get("source_url"))
                or (task.stronger_source_url if task is not None else None)
            ),
            "verdict": status if verdict else None,
            "rationale": verdict.rationale if verdict else None,
            "payload": {
                "stronger_evidence_count": len(stronger_evidence),
                "supporting_claim_indexes": list(risk.supporting_claim_indexes),
            },
        }
        verification_tasks.append(verification_task)
        risk_signals.append(
            {
                "candidate_slug": risk.candidate_slug,
                "risk_key": risk.risk_key,
                "title": risk.title,
                "summary": risk.summary,
                "severity": risk.severity,
                "status": status,
                "credibility": risk.credibility,
                "score_impact": score_impact,
                "supporting_claim_ids": [],
                "supporting_claim_indexes": list(risk.supporting_claim_indexes),
                "verification_task_ids": [],
                "payload": {
                    "verification_task_index": len(verification_tasks) - 1,
                    "supporting_source_urls": _supporting_source_urls(
                        risk,
                        claim_models,
                        normalized_evidence,
                    ),
                },
            }
        )

    return {
        "evidence_items": normalized_evidence,
        "claims": claims,
        "risk_signals": risk_signals,
        "verification_tasks": verification_tasks,
        "risk_adjustments": build_candidate_risk_adjustments(risk_signals),
    }


def score_impact_for_risk(*, status: str, severity: str) -> int:
    if status != "confirmed":
        return 0
    return RISK_IMPACT_BY_SEVERITY.get(severity, RISK_IMPACT_BY_SEVERITY["low"])


def build_candidate_risk_adjustments(risk_signals: Iterable[Mapping[str, Any]]) -> dict[str, dict]:
    adjustments: dict[str, dict] = {}
    for risk in risk_signals:
        candidate_slug = _clean(risk.get("candidate_slug"))
        if not candidate_slug:
            continue
        current = adjustments.setdefault(
            candidate_slug,
            {
                "risk_adjustment": 0,
                "uncapped_risk_adjustment": 0,
                "confirmed_risk_count": 0,
            },
        )
        if risk.get("status") != "confirmed":
            continue
        impact = int(risk.get("score_impact") or 0)
        if impact == 0:
            impact = score_impact_for_risk(
                status="confirmed",
                severity=_clean(risk.get("severity")),
            )
        current["uncapped_risk_adjustment"] += impact
        current["confirmed_risk_count"] += 1
        current["risk_adjustment"] = max(
            current["uncapped_risk_adjustment"],
            MAX_CANDIDATE_RISK_ADJUSTMENT,
        )
    return adjustments


def _claim_dicts(claims: list[ExtractedClaim], evidence_items: list[dict]) -> list[dict]:
    rows = []
    for claim in claims:
        source_index, source = _evidence_for_claim(claim, evidence_items)
        row = claim.model_dump()
        row["source_evidence_index"] = source_index
        row["content_hash"] = content_hash(
            {
                "candidate_slug": claim.candidate_slug,
                "source_type": claim.source_type,
                "source_url": claim.source_url,
                "title": claim.title,
                "summary": claim.summary,
                "citation_text": claim.citation_text,
            }
        )
        row["source_content_hash"] = source.get("content_hash")
        rows.append(row)
    return rows


def _group_risks(risks: list[RiskCluster], claims: list[ExtractedClaim]) -> list[RiskCluster]:
    grouped: dict[tuple[str, str], RiskCluster] = {}
    for risk in risks:
        normalized_key = _normalize_risk_key(risk.risk_key)
        risk = risk.model_copy(update={"risk_key": normalized_key})
        key = (risk.candidate_slug, normalized_key)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = risk
            continue
        supporting_indexes = sorted(
            {
                *existing.supporting_claim_indexes,
                *risk.supporting_claim_indexes,
            }
        )
        grouped[key] = existing.model_copy(
            update={
                "summary": _combine_summaries(existing.summary, risk.summary),
                "severity": _max_severity(existing.severity, risk.severity),
                "status": _initial_group_status(supporting_indexes, claims),
                "credibility": _max_credibility(existing.credibility, risk.credibility),
                "supporting_claim_indexes": supporting_indexes,
            }
        )
    return list(grouped.values())


def _stronger_evidence_for_risk(
    risk: RiskCluster,
    claims: list[ExtractedClaim],
    evidence_items: list[dict],
) -> list[dict]:
    stronger = []
    for index in risk.supporting_claim_indexes:
        if not 0 <= index < len(claims):
            continue
        _, evidence = _evidence_for_claim(claims[index], evidence_items)
        if not evidence or evidence["source_type"] not in STRONG_VERIFICATION_SOURCE_TYPES:
            continue
        provider_evidence = dict(evidence)
        provider_evidence["original_source_type"] = evidence["source_type"]
        provider_evidence["source_type"] = PROVIDER_STRONG_SOURCE_TYPE_ALIASES.get(
            evidence["source_type"],
            evidence["source_type"],
        )
        stronger.append(provider_evidence)
    return stronger


def _supporting_source_urls(
    risk: RiskCluster,
    claims: list[ExtractedClaim],
    evidence_items: list[dict],
) -> list[str]:
    urls = []
    for index in risk.supporting_claim_indexes:
        if 0 <= index < len(claims):
            _, evidence = _evidence_for_claim(claims[index], evidence_items)
            source_url = _clean(evidence.get("source_url"))
            if source_url:
                urls.append(source_url)
    return urls


def _evidence_for_claim(
    claim: ExtractedClaim,
    evidence_items: list[dict],
) -> tuple[int | None, dict]:
    claim_url = _clean(claim.source_url)
    if claim_url:
        for index, evidence in enumerate(evidence_items):
            if _clean(evidence.get("source_url")) == claim_url:
                return index, evidence

    for index, evidence in enumerate(evidence_items):
        if (
            _clean(evidence.get("candidate_slug")) == claim.candidate_slug
            and _clean(evidence.get("source_type")) == claim.source_type
            and (
                _clean(evidence.get("summary")) == claim.summary
                or _clean(evidence.get("title")) == claim.title
            )
        ):
            return index, evidence
    return None, {}


def _initial_group_status(indexes: list[int], claims: list[ExtractedClaim]) -> str:
    supporting = [claims[index] for index in indexes if 0 <= index < len(claims)]
    if supporting and all(
        claim.credibility == "low" or claim.source_type in COMMUNITY_SOURCE_TYPES
        for claim in supporting
    ):
        return "unverified"
    return "unresolved"


def _combine_summaries(left: str, right: str) -> str:
    if right in left:
        return left
    if left in right:
        return right
    return _shorten(f"{left} {right}", limit=800)


def _max_severity(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order.get(left, 0) >= order.get(right, 0) else right


def _max_credibility(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order.get(left, 0) >= order.get(right, 0) else right


def _drop_raw_content(values: Mapping[str, Any]) -> dict:
    raw_keys = {
        "raw_content",
        "raw_thread",
        "full_thread",
        "full_text",
        "full_body",
        "community_thread",
    }
    return {str(key): value for key, value in values.items() if str(key) not in raw_keys}


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _value(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _shorten(value: str, *, limit: int = MAX_EVIDENCE_TEXT_LENGTH) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...[truncated]"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _normalize_risk_key(value: object) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", _clean(value).lower()).strip("_")
    return normalized or "general_risk"
