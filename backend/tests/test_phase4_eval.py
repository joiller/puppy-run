import json
from types import SimpleNamespace

import puppyrun_eval.__main__ as eval_cli
from puppyrun_agent.llm_providers import (
    DeepSeekLLMProvider,
    ExtractedClaim,
    ExtractedClaims,
    ProviderResponseError,
    RiskCluster,
    RiskClusters,
    RiskSynthesis,
    VerificationPlan,
    VerificationTaskPlan,
    VerificationVerdict,
)
from puppyrun_api.config import Settings
from puppyrun_eval.__main__ import main
from puppyrun_eval.cases import EvalContext, get_suite
from puppyrun_eval.reports import write_reports
from puppyrun_eval.runner import (
    CaseResult,
    FailureCategory,
    ResultStatus,
    SuiteResult,
    run_suite,
    sanitize_failure_message,
)
from puppyrun_eval.scoring import assert_workflow_regression_contract


class _FakeChatCompletions:
    def __init__(self, payloads: list[dict] | None = None, error: Exception | None = None) -> None:
        self.payloads = list(payloads or [])
        self.error = error

    def create(self, **kwargs):
        if self.error is not None:
            raise self.error
        payload = self.payloads.pop(0)
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(payload)},
                }
            ]
        }


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class _FakeDeepSeekClient:
    def __init__(self, payloads: list[dict] | None = None, error: Exception | None = None) -> None:
        self.chat = _FakeChat(_FakeChatCompletions(payloads, error))


def _fake_deepseek_provider(payloads: list[dict] | None = None, error: Exception | None = None):
    return DeepSeekLLMProvider(
        api_key="test-key",
        model="deepseek-test",
        client=_FakeDeepSeekClient(payloads, error),
    )


class _ConfirmedSecondCommunityRiskProvider:
    def __init__(self) -> None:
        self.extract_call_count = 0

    def extract_claims(self, evidence_items: list[dict]):
        self.extract_call_count += 1
        if self.extract_call_count == 1:
            claims = [
                SimpleNamespace(
                    candidate_slug="langgraph",
                    source_type="official_docs",
                    title="LangGraph documents durable execution",
                ),
                SimpleNamespace(
                    candidate_slug="langgraph",
                    source_type="github_release",
                    title="LangGraph release includes checkpoint changes",
                ),
                SimpleNamespace(
                    candidate_slug="crewai",
                    source_type="hacker_news",
                    title="Community reports operational risk",
                ),
            ]
        else:
            claims = [
                SimpleNamespace(
                    candidate_slug="crewai",
                    source_type="reddit",
                    title="Community-only maintenance risk",
                )
            ]
        return SimpleNamespace(claims=claims)

    def cluster_risks(self, claims: list):
        return SimpleNamespace(
            risks=[
                SimpleNamespace(
                    title="Operational Complexity",
                    status="unverified",
                    credibility="medium",
                ),
                SimpleNamespace(
                    title="Maintenance Staleness",
                    status="confirmed",
                    credibility="medium",
                ),
            ]
        )

    def plan_verification(self, risks: list):
        return SimpleNamespace(
            tasks=[SimpleNamespace(stronger_source_type="github_release")]
        )

    def verify_risk(self, risk, *, stronger_evidence: list[dict]):
        return SimpleNamespace(verdict="confirmed", source_type="github_release")

    def synthesize_risks(self, risks: list):
        return SimpleNamespace(
            summary="The maintenance risk remains confirmed after stronger evidence.",
            confirmed_risks=["Maintenance Staleness"],
            unresolved_risks=[],
            contradicted_risks=[],
            unverified_risks=[],
        )


class _PassingPhase4Provider:
    def extract_claims(self, evidence_items: list[dict]):
        claims = []
        for index, evidence in enumerate(evidence_items):
            candidate_slug = str(evidence.get("candidate_slug") or "langgraph")
            source_type = str(evidence.get("source_type") or "official_docs")
            credibility = str(evidence.get("credibility") or "medium")
            if source_type in {"reddit", "hacker_news", "stack_exchange"}:
                credibility = "low"
            elif source_type in {"official_docs", "github_release"}:
                credibility = "high"
            risk_key = "maintenance" if index == 0 or source_type != "official_docs" else None
            claims.append(
                ExtractedClaim(
                    candidate_slug=candidate_slug,
                    source_type=source_type,
                    source_url=str(evidence.get("source_url") or ""),
                    title=str(evidence.get("title") or f"{candidate_slug} evidence"),
                    summary=str(
                        evidence.get("summary")
                        or f"{candidate_slug} maintenance and observability evidence."
                    ),
                    citation_text=str(evidence.get("citation_text") or "Evidence citation."),
                    credibility=credibility,
                    confidence=86 if credibility != "low" else 44,
                    risk_key=risk_key,
                )
            )
        return ExtractedClaims(claims=claims)

    def cluster_risks(self, claims: list):
        risks = []
        for index, claim in enumerate(claims):
            if not claim.risk_key:
                continue
            low_trust = claim.credibility == "low" or claim.source_type in {
                "reddit",
                "hacker_news",
                "stack_exchange",
            }
            risks.append(
                RiskCluster(
                    candidate_slug=claim.candidate_slug,
                    risk_key=claim.risk_key,
                    title="Maintenance Risk",
                    summary=claim.summary,
                    severity="medium",
                    status="unverified" if low_trust else "unresolved",
                    credibility=claim.credibility,
                    supporting_claim_indexes=[index],
                )
            )
        return RiskClusters(risks=risks)

    def plan_verification(self, risks: list):
        return VerificationPlan(
            tasks=[
                VerificationTaskPlan(
                    candidate_slug=risk.candidate_slug,
                    risk_key=risk.risk_key,
                    verification_question=(
                        f"Check release and official sources for {risk.candidate_slug} risk."
                    ),
                    stronger_source_type="github_release",
                    stronger_source_url=f"https://github.com/example/{risk.candidate_slug}/releases",
                )
                for risk in risks
            ]
        )

    def verify_risk(self, risk, *, stronger_evidence: list[dict]):
        if risk.candidate_slug == "langgraph":
            return VerificationVerdict(
                verdict="confirmed",
                rationale="GitHub and official source evidence confirms maintenance risk.",
                source_type="github_issue",
                source_url="https://github.com/langchain-ai/langgraph/issues",
            )
        return VerificationVerdict(
            verdict="unresolved",
            rationale="Stronger evidence did not confirm this maintenance risk.",
            source_type="github_issue",
            source_url=f"https://github.com/example/{risk.candidate_slug}/issues",
        )

    def synthesize_risks(self, risks: list):
        return RiskSynthesis(
            summary="Risk synthesis reviewed maintenance and observability impacts.",
            confirmed_risks=[risk.title for risk in risks if risk.status == "confirmed"],
            unresolved_risks=[risk.title for risk in risks if risk.status == "unresolved"],
            contradicted_risks=[risk.title for risk in risks if risk.status == "contradicted"],
            unverified_risks=[risk.title for risk in risks if risk.status == "unverified"],
        )


class _WorkflowFailingProvider(_PassingPhase4Provider):
    def __init__(self) -> None:
        self.extract_call_count = 0

    def extract_claims(self, evidence_items: list[dict]):
        self.extract_call_count += 1
        if self.extract_call_count > 2:
            raise ProviderResponseError("DeepSeek workflow failure api_key=secret token abc123")
        return super().extract_claims(evidence_items)


def _passing_contract_payloads() -> list[dict]:
    return [
        {
            "claims": [
                {
                    "candidate_slug": "langgraph",
                    "source_type": "official_docs",
                    "source_url": "https://docs.example/langgraph",
                    "title": "LangGraph documents durable execution",
                    "summary": "Official docs describe durable execution support.",
                    "citation_text": "Durable execution support is documented.",
                    "credibility": "high",
                    "confidence": 91,
                    "risk_key": None,
                },
                {
                    "candidate_slug": "langgraph",
                    "source_type": "github_release",
                    "source_url": "https://github.example/langgraph/releases/v1",
                    "title": "LangGraph release includes checkpoint changes",
                    "summary": "A release note describes checkpoint and runtime changes.",
                    "citation_text": "Checkpoint and runtime changes shipped.",
                    "credibility": "high",
                    "confidence": 88,
                    "risk_key": "maintenance",
                },
                {
                    "candidate_slug": "crewai",
                    "source_type": "hacker_news",
                    "source_url": "https://news.example/item/1",
                    "title": "Community reports operational risk",
                    "summary": "Community discussion reports maintenance risk.",
                    "citation_text": "Several users report maintenance risk.",
                    "credibility": "low",
                    "confidence": 44,
                    "risk_key": "community_risk",
                },
            ]
        },
        {
            "claims": [
                {
                    "candidate_slug": "crewai",
                    "source_type": "reddit",
                    "source_url": "https://reddit.example/r/agents/1",
                    "title": "Community-only maintenance risk",
                    "summary": "Community-only discussion reports stale maintenance risk.",
                    "citation_text": "Users say maintenance feels stale.",
                    "credibility": "low",
                    "confidence": 42,
                    "risk_key": "maintenance",
                }
            ]
        },
        {
            "risks": [
                {
                    "candidate_slug": "crewai",
                    "risk_key": "maintenance",
                    "title": "Maintenance Staleness",
                    "summary": "Community-only claims suggest stale maintenance.",
                    "severity": "medium",
                    "status": "unverified",
                    "credibility": "low",
                    "supporting_claim_indexes": [0],
                }
            ]
        },
        {
            "tasks": [
                {
                    "candidate_slug": "crewai",
                    "risk_key": "maintenance",
                    "verification_question": "Check official release history for maintenance.",
                    "stronger_source_type": "github_release",
                    "stronger_source_url": "https://github.example/crewai/releases",
                }
            ]
        },
        {
            "verdict": "confirmed",
            "rationale": "A release source confirms an ongoing maintenance gap.",
            "source_type": "github_release",
            "source_url": "https://github.example/crewai/releases",
        },
        {
            "summary": "The maintenance risk remains confirmed after stronger evidence.",
            "confirmed_risks": ["Maintenance Staleness"],
            "unresolved_risks": [],
            "contradicted_risks": [],
            "unverified_risks": [],
        },
    ]


def test_phase4_live_suite_is_registered() -> None:
    suite = get_suite("phase4-live")

    assert suite.suite_id == "phase4-live"
    assert suite.cases
    assert {case.kind for case in suite.cases} >= {"provider_contract", "workflow_regression"}
    assert all(case.required for case in suite.cases)


def test_phase4_live_runs_deepseek_provider_contract_cases_without_network() -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_model="deepseek-test",
    )
    suite_result = run_suite(
        "phase4-live",
        settings=settings,
        provider=_PassingPhase4Provider(),
    )

    statuses = {case.case_id: case.status for case in suite_result.case_results}
    required_flags = {case.case_id: case.required for case in suite_result.case_results}

    assert suite_result.status == ResultStatus.PASS
    assert suite_result.exit_code == 0
    assert statuses == {
        "deepseek-claim-extraction-contract": ResultStatus.PASS,
        "deepseek-low-trust-risk-contract": ResultStatus.PASS,
        "deepseek-verification-contract": ResultStatus.PASS,
        "workflow-regression-framework-selection": ResultStatus.PASS,
    }
    assert required_flags == {
        "deepseek-claim-extraction-contract": True,
        "deepseek-low-trust-risk-contract": True,
        "deepseek-verification-contract": True,
        "workflow-regression-framework-selection": True,
    }
    workflow_result = next(
        case
        for case in suite_result.case_results
        if case.case_id == "workflow-regression-framework-selection"
    )
    assert workflow_result.observations is not None
    assert workflow_result.observations["candidate_count"] >= 3
    assert workflow_result.observations["criterion_count"] >= 5
    assert workflow_result.observations["claim_count"] > 0
    assert workflow_result.observations["risk_signal_count"] > 0
    assert workflow_result.observations["verification_task_count"] > 0
    assert workflow_result.observations["tool_call_count"] > 0


def test_provider_contract_cases_pass_through_deepseek_json_parsing_without_network() -> None:
    provider = _fake_deepseek_provider(_passing_contract_payloads())
    context = EvalContext(provider=provider)
    contract_cases = [
        case for case in get_suite("phase4-live").cases if case.kind == "provider_contract"
    ]

    outcomes = [case.run(context) for case in contract_cases]

    assert [case.case_id for case in contract_cases] == [
        "deepseek-claim-extraction-contract",
        "deepseek-low-trust-risk-contract",
        "deepseek-verification-contract",
    ]
    assert all(outcome is not None for outcome in outcomes)
    assert all(outcome.observations for outcome in outcomes if outcome is not None)
    assert provider.client.chat.completions.payloads == []


def test_deepseek_verification_contract_normalizes_stronger_source_aliases() -> None:
    provider = _fake_deepseek_provider(
        [
            {
                "tasks": [
                    {
                        "candidate_slug": "crewai",
                        "risk_key": "maintenance",
                        "verification_question": "Check official release history for maintenance.",
                        "stronger_source_type": "official_release",
                        "stronger_source_url": "https://github.example/crewai/releases",
                    }
                ]
            },
            {
                "verdict": "confirmed",
                "rationale": "An official release source confirms the maintenance risk.",
                "source_type": "official_release",
                "source_url": "https://github.example/crewai/releases",
            },
            {
                "summary": "The maintenance risk remains confirmed after stronger evidence.",
                "confirmed_risks": ["Maintenance Staleness"],
                "unresolved_risks": [],
                "contradicted_risks": [],
                "unverified_risks": [],
            },
        ]
    )
    context = EvalContext(provider=provider)
    verification_case = next(
        case
        for case in get_suite("phase4-live").cases
        if case.case_id == "deepseek-verification-contract"
    )

    outcome = verification_case.run(context)

    assert outcome is not None
    assert outcome.observations["task_source_types"] == ["github_release"]
    assert outcome.observations["verdict"] == "confirmed"
    assert outcome.observations["verdict_source_type"] == "github_release"


def test_workflow_regression_contract_allows_zero_adjustments_with_risk_facts() -> None:
    workspace = SimpleNamespace(
        session=SimpleNamespace(status="completed"),
        active_version=SimpleNamespace(
            status="completed",
            gap_analysis={
                "risk_adjusted_scores": {
                    "langgraph": {
                        "base_score": 90,
                        "risk_adjustment": 0,
                        "uncapped_risk_adjustment": 0,
                        "adjusted_score": 90,
                        "confirmed_risk_count": 0,
                    },
                    "openai-agents-python": {
                        "base_score": 84,
                        "risk_adjustment": 0,
                        "uncapped_risk_adjustment": 0,
                        "adjusted_score": 84,
                        "confirmed_risk_count": 0,
                    },
                    "crewai": {
                        "base_score": 78,
                        "risk_adjustment": 0,
                        "uncapped_risk_adjustment": 0,
                        "adjusted_score": 78,
                        "confirmed_risk_count": 0,
                    },
                }
            },
            adr=(
                "ADR 0002: LangGraph selected. Risk reasoning: Maintenance Risk remains "
                "visible after verification, but no confirmed risk adjustment was applied."
            ),
        ),
        candidates=[
            SimpleNamespace(id="candidate-1", slug="langgraph", name="LangGraph"),
            SimpleNamespace(
                id="candidate-2",
                slug="openai-agents-python",
                name="OpenAI Agents SDK",
            ),
            SimpleNamespace(id="candidate-3", slug="crewai", name="CrewAI"),
        ],
        criteria=[object(), object(), object(), object(), object()],
        evidence_items=[
            SimpleNamespace(source_type="github_repo", payload={}),
            SimpleNamespace(source_type="official_docs", payload={"phase3": True}),
        ],
        claims=[SimpleNamespace()],
        risk_signals=[SimpleNamespace(title="Maintenance Risk")],
        verification_tasks=[SimpleNamespace()],
        tool_calls=[SimpleNamespace()],
        recommendations=[
            SimpleNamespace(
                recommended_candidate_id="candidate-1",
                summary="LangGraph is recommended for the framework selection.",
                rationale={
                    "ranked_candidates": [
                        {
                            "slug": "langgraph",
                            "risk_adjustment": 0,
                            "adjusted_score": 90,
                        }
                    ]
                },
            )
        ],
    )

    observations = assert_workflow_regression_contract(workspace)

    assert observations["recommended_slug"] == "langgraph"
    assert observations["risk_adjusted_slugs"] == [
        "crewai",
        "langgraph",
        "openai-agents-python",
    ]


def test_workflow_regression_provider_failure_fails_instead_of_falling_back() -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_model="deepseek-test",
    )
    suite_result = run_suite(
        "phase4-live",
        settings=settings,
        provider=_WorkflowFailingProvider(),
    )

    results = {case.case_id: case for case in suite_result.case_results}
    workflow_result = results["workflow-regression-framework-selection"]

    assert suite_result.status == ResultStatus.FAIL
    assert workflow_result.status == ResultStatus.FAIL
    assert workflow_result.failure_category == FailureCategory.PROVIDER_RESPONSE
    assert workflow_result.failure_message is not None
    assert "secret" not in workflow_result.failure_message
    assert "abc123" not in workflow_result.failure_message
    assert "[redacted]" in workflow_result.failure_message


def test_confirmed_community_risk_anywhere_fails_even_if_credibility_is_mislabeled() -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_model="deepseek-test",
    )
    suite_result = run_suite(
        "phase4-live",
        settings=settings,
        provider=_ConfirmedSecondCommunityRiskProvider(),
    )

    results = {case.case_id: case for case in suite_result.case_results}
    low_trust_result = results["deepseek-low-trust-risk-contract"]

    assert suite_result.status == ResultStatus.FAIL
    assert suite_result.exit_code == 1
    assert low_trust_result.status == ResultStatus.FAIL
    assert low_trust_result.failure_category == FailureCategory.QUALITY_REGRESSION
    assert low_trust_result.failure_message == (
        "Expected community-only low-trust risk to remain unconfirmed."
    )


def test_phase4_live_redacts_provider_response_failures() -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="test-key",
        deepseek_model="deepseek-test",
    )
    suite_result = run_suite(
        "phase4-live",
        settings=settings,
        provider=_fake_deepseek_provider(error=RuntimeError("api_key=secret token abc123")),
    )

    first_result = suite_result.case_results[0]
    assert first_result.status == ResultStatus.FAIL
    assert first_result.failure_category == FailureCategory.PROVIDER_RESPONSE
    assert first_result.failure_message is not None
    assert "secret" not in first_result.failure_message
    assert "abc123" not in first_result.failure_message
    assert "[redacted]" in first_result.failure_message


def test_missing_deepseek_key_blocks_runner_and_cli(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PUPPYRUN_LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("PUPPYRUN_DEEPSEEK_API_KEY", raising=False)

    suite_result = run_suite("phase4-live")

    assert suite_result.status == ResultStatus.BLOCKED
    assert suite_result.exit_code == 2
    assert all(case.status == ResultStatus.BLOCKED for case in suite_result.case_results)
    assert {
        case.failure_category for case in suite_result.case_results
    } == {FailureCategory.PROVIDER_UNAVAILABLE}

    exit_code = main(["run", "--suite", "phase4-live", "--report-dir", str(tmp_path)])

    assert exit_code == 2


def test_sanitizes_key_like_failure_text() -> None:
    message = (
        "provider failed with api_key=sk-private "
        "Authorization: Bearer live-secret token abc123"
    )

    sanitized = sanitize_failure_message(message)

    assert "sk-private" not in sanitized
    assert "live-secret" not in sanitized
    assert "abc123" not in sanitized
    assert "[redacted]" in sanitized


def test_cli_sanitizes_runner_errors_on_stderr(monkeypatch, capsys) -> None:
    def raise_token_error(suite_id: str):
        raise ValueError(f"unknown {suite_id} api_key=sk-private token abc123")

    monkeypatch.setattr(eval_cli, "run_suite", raise_token_error)

    exit_code = main(["run", "--suite", "phase4-live"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "sk-private" not in captured.err
    assert "abc123" not in captured.err
    assert "[redacted]" in captured.err


def test_writes_minimal_json_and_markdown_reports(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PUPPYRUN_LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("PUPPYRUN_DEEPSEEK_API_KEY", raising=False)
    suite_result = run_suite("phase4-live")

    report_paths = write_reports(suite_result, report_dir=tmp_path)

    assert report_paths.json_path.exists()
    assert report_paths.markdown_path.exists()
    report_payload = json.loads(report_paths.json_path.read_text())
    assert report_payload["suite_id"] == "phase4-live"
    assert report_payload["status"] == "blocked"
    assert report_payload["report_schema_version"] == "phase4-live-report-v1"
    assert report_payload["generated_at"]
    assert report_payload["summary"]["case_count"] == len(report_payload["case_results"])
    assert report_payload["summary"]["blocked_count"] == len(report_payload["case_results"])
    assert report_payload["case_results"]
    markdown = report_paths.markdown_path.read_text()
    assert "# phase4-live" in markdown
    assert "- Cases: 0 pass, 0 fail" in markdown


def test_reports_include_observations_usage_and_recursive_redaction(tmp_path) -> None:
    suite_result = SuiteResult(
        suite_id="phase4-live",
        status=ResultStatus.FAIL,
        case_results=[
            CaseResult(
                suite_id="phase4-live",
                case_id="deepseek-claim-extraction-contract",
                case_kind="provider_contract",
                required=True,
                status=ResultStatus.PASS,
                failure_category=None,
                failure_message=None,
                provider_name="deepseek",
                model_name="deepseek-test",
                duration_seconds=0.25,
                started_at="2026-06-20T00:00:00+00:00",
                finished_at="2026-06-20T00:00:01+00:00",
                usage={
                    "input_tokens": 12,
                    "output_tokens": 8,
                    "cost_usd": 0.001,
                    "bearer": "Bearer live-secret",
                },
                observations={
                    "claim_count": 3,
                    "api_key": "sk-private",
                    "note": "token abc123",
                    "community_thread": "full raw thread text",
                },
            ),
            CaseResult(
                suite_id="phase4-live",
                case_id="workflow-regression-framework-selection",
                case_kind="workflow_regression",
                required=True,
                status=ResultStatus.FAIL,
                failure_category=FailureCategory.PROVIDER_RESPONSE,
                failure_message="provider failed with api_key=sk-private token abc123",
                provider_name="deepseek",
                model_name="deepseek-test",
                duration_seconds=1.5,
                started_at="2026-06-20T00:00:02+00:00",
                finished_at="2026-06-20T00:00:03+00:00",
            ),
        ],
        provider_name="deepseek",
        model_name="deepseek-test",
        duration_seconds=1.75,
        started_at="2026-06-20T00:00:00+00:00",
        finished_at="2026-06-20T00:00:03+00:00",
    )

    report_paths = write_reports(suite_result, report_dir=tmp_path)

    json_text = report_paths.json_path.read_text()
    markdown = report_paths.markdown_path.read_text()
    report_payload = json.loads(json_text)
    assert report_payload["summary"] == {
        "blocked_count": 0,
        "case_count": 2,
        "fail_count": 1,
        "pass_count": 1,
        "required_blocked_count": 0,
        "required_count": 2,
        "required_fail_count": 1,
        "required_pass_count": 1,
    }
    assert report_payload["case_results"][0]["usage"]["input_tokens"] == 12
    assert report_payload["case_results"][0]["usage"]["bearer"] == "Bearer [redacted]"
    assert report_payload["case_results"][0]["observations"]["api_key"] == "[redacted]"
    assert report_payload["case_results"][0]["observations"]["community_thread"] == (
        "[redacted raw content]"
    )
    assert "sk-private" not in json_text
    assert "abc123" not in json_text
    assert "live-secret" not in json_text
    assert "sk-private" not in markdown
    assert "abc123" not in markdown
    assert "live-secret" not in markdown
    assert "## Observations" in markdown
    assert "`claim_count`: 3" in markdown
    assert "## Usage" in markdown
    assert "`input_tokens`: 12" in markdown


def test_markdown_report_escapes_table_cells(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PUPPYRUN_LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("PUPPYRUN_DEEPSEEK_API_KEY", raising=False)
    suite_result = run_suite("phase4-live")
    first_result = suite_result.case_results[0]
    escaped_result = suite_result.__class__(
        suite_id=suite_result.suite_id,
        status=suite_result.status,
        case_results=[
            first_result.__class__(
                suite_id=first_result.suite_id,
                case_id="case|with|pipes",
                case_kind=first_result.case_kind,
                required=first_result.required,
                status=first_result.status,
                failure_category=first_result.failure_category,
                failure_message="message with | pipe",
                provider_name=first_result.provider_name,
                model_name=first_result.model_name,
                duration_seconds=first_result.duration_seconds,
                started_at=first_result.started_at,
                finished_at=first_result.finished_at,
                usage=first_result.usage,
            )
        ],
        provider_name=suite_result.provider_name,
        model_name=suite_result.model_name,
        duration_seconds=suite_result.duration_seconds,
        started_at=suite_result.started_at,
        finished_at=suite_result.finished_at,
    )

    report_paths = write_reports(escaped_result, report_dir=tmp_path)

    markdown = report_paths.markdown_path.read_text()
    assert "case\\|with\\|pipes" in markdown
    assert "message with \\| pipe" in markdown
