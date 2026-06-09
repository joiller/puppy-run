from puppyrun_agent.llm_providers import (
    ExtractedClaim,
    ExtractedClaims,
    RiskCluster,
    RiskClusters,
    VerificationPlan,
    VerificationTaskPlan,
)
from puppyrun_agent.phase3 import (
    build_candidate_risk_adjustments,
    build_risk_verification_pipeline,
    normalize_evidence_items,
)


def test_normalize_evidence_maps_source_type_to_credibility() -> None:
    normalized = normalize_evidence_items(
        [
            {
                "candidate_slug": "langgraph",
                "source_type": "official_docs",
                "source_url": "https://docs.example/langgraph",
                "title": "Docs",
                "summary": "Official checkpointing docs.",
                "citation_text": "Checkpointing docs.",
            },
            {
                "candidate_slug": "langgraph",
                "source_type": "github_issue",
                "source_url": "https://github.example/issue",
                "title": "Issue",
                "summary": "Maintainer triage discussion.",
                "citation_text": "Triage discussion.",
            },
            {
                "candidate_slug": "crewai",
                "source_type": "reddit",
                "source_url": "https://reddit.example/r/agents/1",
                "title": "Community thread",
                "summary": "Community discussion reports maintenance risk.",
                "citation_text": "Maintenance risk.",
                "credibility": "high",
                "raw_content": "raw community post body should not pass through",
            },
        ]
    )

    assert [item["credibility"] for item in normalized] == ["high", "medium", "low"]
    assert all(item["content_hash"] for item in normalized)
    assert "raw_content" not in normalized[2]
    assert "raw community post body" not in str(normalized[2])


def test_community_evidence_creates_unverified_risk_without_score_impact() -> None:
    result = build_risk_verification_pipeline(
        [
            {
                "candidate_slug": "crewai",
                "source_type": "hacker_news",
                "source_url": "https://news.ycombinator.com/item?id=1",
                "title": "CrewAI discussion",
                "summary": (
                    "Community discussion reports critical maintenance risk and stale issues."
                ),
                "citation_text": "Maintenance risk and stale issues.",
                "raw_content": "full community thread should not be returned",
            }
        ]
    )

    assert result["risk_signals"][0]["status"] == "unverified"
    assert result["risk_signals"][0]["score_impact"] == 0
    assert result["verification_tasks"][0]["stronger_source_type"] == "official_docs"
    assert "full community thread" not in str(result)


def test_stronger_evidence_confirms_or_contradicts_community_risks() -> None:
    result = build_risk_verification_pipeline(
        [
            {
                "candidate_slug": "crewai",
                "source_type": "reddit",
                "source_url": "https://reddit.example/r/agents/1",
                "title": "CrewAI maintenance thread",
                "summary": (
                    "Community discussion reports critical maintenance risk and stale issues."
                ),
                "citation_text": "Maintenance risk.",
            },
            {
                "candidate_slug": "crewai",
                "source_type": "github_issue",
                "source_url": "https://github.example/crewai/issues/1",
                "title": "Critical maintenance issue",
                "summary": "Critical maintenance risk has repeated unresolved incidents.",
                "citation_text": "Critical maintenance issue.",
            },
            {
                "candidate_slug": "langgraph",
                "source_type": "reddit",
                "source_url": "https://reddit.example/r/agents/2",
                "title": "LangGraph maintenance thread",
                "summary": "Community discussion reports maintenance risk.",
                "citation_text": "Maintenance risk.",
            },
            {
                "candidate_slug": "langgraph",
                "source_type": "official_docs",
                "source_url": "https://docs.example/langgraph",
                "title": "LangGraph docs",
                "summary": "Official docs do not report stale maintenance risk.",
                "citation_text": "No stale maintenance risk.",
            },
        ]
    )
    risks_by_slug = {risk["candidate_slug"]: risk for risk in result["risk_signals"]}

    assert risks_by_slug["crewai"]["status"] == "confirmed"
    assert risks_by_slug["crewai"]["score_impact"] == -8
    assert risks_by_slug["langgraph"]["status"] == "contradicted"
    assert risks_by_slug["langgraph"]["score_impact"] == 0


def test_risk_adjustments_only_count_confirmed_risks_and_cap_per_candidate() -> None:
    adjustments = build_candidate_risk_adjustments(
        [
            {
                "candidate_slug": "crewai",
                "status": "confirmed",
                "severity": "high",
                "score_impact": -8,
            },
            {
                "candidate_slug": "crewai",
                "status": "confirmed",
                "severity": "medium",
                "score_impact": -5,
            },
            {
                "candidate_slug": "crewai",
                "status": "confirmed",
                "severity": "high",
                "score_impact": -8,
            },
            {
                "candidate_slug": "langgraph",
                "status": "unresolved",
                "severity": "high",
                "score_impact": -8,
            },
            {
                "candidate_slug": "autogen",
                "status": "contradicted",
                "severity": "high",
                "score_impact": -8,
            },
        ]
    )

    assert adjustments["crewai"] == {
        "risk_adjustment": -15,
        "uncapped_risk_adjustment": -21,
        "confirmed_risk_count": 3,
    }
    assert adjustments["langgraph"]["risk_adjustment"] == 0
    assert adjustments["autogen"]["risk_adjustment"] == 0


def test_pipeline_groups_by_normalized_risk_key() -> None:
    provider = DuplicateRiskProvider()

    result = build_risk_verification_pipeline(
        [
            {
                "candidate_slug": "crewai",
                "source_type": "reddit",
                "source_url": "https://reddit.example/r/agents/1",
                "title": "CrewAI maintenance thread",
                "summary": "Community discussion reports maintenance risk.",
                "citation_text": "Maintenance risk.",
            },
            {
                "candidate_slug": "crewai",
                "source_type": "hacker_news",
                "source_url": "https://news.ycombinator.com/item?id=1",
                "title": "CrewAI stale issue thread",
                "summary": "Community discussion reports stale maintenance.",
                "citation_text": "Stale maintenance.",
            },
        ],
        provider=provider,
    )

    assert [risk["risk_key"] for risk in result["risk_signals"]] == ["maintenance_risk"]
    assert result["risk_signals"][0]["supporting_claim_indexes"] == [0, 1]


def test_pipeline_uses_claim_source_url_when_claim_order_differs_from_evidence() -> None:
    provider = ReorderedClaimProvider()

    result = build_risk_verification_pipeline(
        [
            {
                "candidate_slug": "crewai",
                "source_type": "reddit",
                "source_url": "https://reddit.example/r/agents/1",
                "title": "CrewAI maintenance thread",
                "summary": "Community discussion reports maintenance risk.",
                "citation_text": "Maintenance risk.",
            },
            {
                "candidate_slug": "crewai",
                "source_type": "github_issue",
                "source_url": "https://github.example/crewai/issues/1",
                "title": "Critical maintenance issue",
                "summary": "Critical maintenance risk has repeated unresolved incidents.",
                "citation_text": "Critical maintenance issue.",
            },
        ],
        provider=provider,
    )

    assert result["risk_signals"][0]["status"] == "confirmed"
    assert result["risk_signals"][0]["payload"]["supporting_source_urls"] == [
        "https://github.example/crewai/issues/1"
    ]
    assert result["verification_tasks"][0]["stronger_source_url"] == (
        "https://github.example/crewai/issues/1"
    )


def test_official_release_and_source_code_can_verify_risks() -> None:
    release_result = build_risk_verification_pipeline(
        [
            {
                "candidate_slug": "crewai",
                "source_type": "reddit",
                "source_url": "https://reddit.example/r/agents/1",
                "title": "CrewAI maintenance thread",
                "summary": "Community discussion reports critical maintenance risk.",
                "citation_text": "Maintenance risk.",
            },
            {
                "candidate_slug": "crewai",
                "source_type": "official_release",
                "source_url": "https://github.example/crewai/releases/v1",
                "title": "CrewAI maintenance release",
                "summary": "Critical maintenance risk has repeated unresolved incidents.",
                "citation_text": "Critical maintenance release.",
            },
        ]
    )
    source_result = build_risk_verification_pipeline(
        [
            {
                "candidate_slug": "langgraph",
                "source_type": "reddit",
                "source_url": "https://reddit.example/r/agents/2",
                "title": "LangGraph maintenance thread",
                "summary": "Community discussion reports maintenance risk.",
                "citation_text": "Maintenance risk.",
            },
            {
                "candidate_slug": "langgraph",
                "source_type": "source_code",
                "source_url": "https://github.example/langgraph/blob/main/runtime.py",
                "title": "LangGraph source code",
                "summary": "Source code documents supported maintenance behavior.",
                "citation_text": "Supported maintenance behavior.",
            },
        ]
    )

    assert release_result["risk_signals"][0]["status"] == "confirmed"
    assert release_result["risk_signals"][0]["score_impact"] == -8
    assert source_result["risk_signals"][0]["status"] == "contradicted"
    assert source_result["risk_signals"][0]["score_impact"] == 0


class DuplicateRiskProvider:
    def extract_claims(self, evidence_items: list[dict]) -> ExtractedClaims:
        return ExtractedClaims(
            claims=[
                ExtractedClaim(
                    candidate_slug=item["candidate_slug"],
                    source_type=item["source_type"],
                    source_url=item["source_url"],
                    title=item["title"],
                    summary=item["summary"],
                    citation_text=item["citation_text"],
                    credibility=item["credibility"],
                    confidence=55,
                    risk_key="maintenance_risk",
                )
                for item in evidence_items
            ]
        )

    def cluster_risks(self, claims: list[ExtractedClaim]) -> RiskClusters:
        return RiskClusters(
            risks=[
                RiskCluster(
                    candidate_slug="crewai",
                    risk_key="Maintenance Risk",
                    title="Maintenance Risk",
                    summary=claims[0].summary,
                    severity="medium",
                    status="unverified",
                    credibility="low",
                    supporting_claim_indexes=[0],
                ),
                RiskCluster(
                    candidate_slug="crewai",
                    risk_key="maintenance_risk",
                    title="Maintenance Risk",
                    summary=claims[1].summary,
                    severity="medium",
                    status="unverified",
                    credibility="low",
                    supporting_claim_indexes=[1],
                ),
            ]
        )

    def plan_verification(self, risks: list[RiskCluster]) -> VerificationPlan:
        return VerificationPlan(
            tasks=[
                VerificationTaskPlan(
                    candidate_slug=risk.candidate_slug,
                    risk_key=risk.risk_key,
                    verification_question="Find official maintenance evidence.",
                    stronger_source_type="official_docs",
                    stronger_source_url=None,
                )
                for risk in risks
            ]
        )

    def verify_risk(self, risk: RiskCluster, *, stronger_evidence: list[dict]):
        raise AssertionError("community-only duplicate risk should not be verified")


class ReorderedClaimProvider:
    def extract_claims(self, evidence_items: list[dict]) -> ExtractedClaims:
        github_issue = evidence_items[1]
        community = evidence_items[0]
        return ExtractedClaims(
            claims=[
                ExtractedClaim(
                    candidate_slug=github_issue["candidate_slug"],
                    source_type=github_issue["source_type"],
                    source_url=github_issue["source_url"],
                    title=github_issue["title"],
                    summary=github_issue["summary"],
                    citation_text=github_issue["citation_text"],
                    credibility=github_issue["credibility"],
                    confidence=75,
                    risk_key="maintenance_risk",
                ),
                ExtractedClaim(
                    candidate_slug=community["candidate_slug"],
                    source_type=community["source_type"],
                    source_url=community["source_url"],
                    title=community["title"],
                    summary=community["summary"],
                    citation_text=community["citation_text"],
                    credibility=community["credibility"],
                    confidence=55,
                    risk_key="maintenance_risk",
                ),
            ]
        )

    def cluster_risks(self, claims: list[ExtractedClaim]) -> RiskClusters:
        return RiskClusters(
            risks=[
                RiskCluster(
                    candidate_slug="crewai",
                    risk_key="maintenance_risk",
                    title="Maintenance Risk",
                    summary=claims[0].summary,
                    severity="high",
                    status="unresolved",
                    credibility="medium",
                    supporting_claim_indexes=[0],
                )
            ]
        )

    def plan_verification(self, risks: list[RiskCluster]) -> VerificationPlan:
        return VerificationPlan(
            tasks=[
                VerificationTaskPlan(
                    candidate_slug=risk.candidate_slug,
                    risk_key=risk.risk_key,
                    verification_question="Verify maintenance risk in GitHub issues.",
                    stronger_source_type="github_issue",
                    stronger_source_url=None,
                )
                for risk in risks
            ]
        )

    def verify_risk(self, risk: RiskCluster, *, stronger_evidence: list[dict]):
        from puppyrun_agent.llm_providers import VerificationVerdict

        assert stronger_evidence[0]["source_url"] == "https://github.example/crewai/issues/1"
        return VerificationVerdict(
            verdict="confirmed",
            rationale="GitHub issue confirms repeated maintenance incidents.",
            source_type="github_issue",
            source_url=stronger_evidence[0]["source_url"],
        )
