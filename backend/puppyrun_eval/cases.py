from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import puppyrun_agent.workflow as workflow_module
from puppyrun_agent.llm_providers import ProviderResponseError, RiskCluster
from puppyrun_agent.workflow import run_phase1_workflow, run_phase2_workflow
from puppyrun_api.db import Base
from puppyrun_api.models import DecisionSession, DecisionSessionStatus, DecisionVersion
from puppyrun_api.repositories.sessions import (
    create_agent_run,
    create_decision_session,
    create_phase2_version_run,
)
from puppyrun_api.repositories.workspace import append_user_message, get_workspace
from puppyrun_eval.scoring import (
    assert_claim_contract,
    assert_low_trust_risk_contract,
    assert_verification_contract,
    assert_workflow_regression_contract,
)

CaseKind = Literal["provider_contract", "workflow_regression"]


class EvalProvider(Protocol):
    def extract_claims(self, evidence_items: list[dict[str, Any]]) -> Any: ...

    def cluster_risks(self, claims: list[Any]) -> Any: ...

    def plan_verification(self, risks: list[RiskCluster]) -> Any: ...

    def verify_risk(
        self,
        risk: RiskCluster,
        *,
        stronger_evidence: list[dict[str, Any]],
    ) -> Any: ...

    def synthesize_risks(self, risks: list[RiskCluster]) -> Any: ...


@dataclass(frozen=True)
class EvalContext:
    provider: EvalProvider


@dataclass(frozen=True)
class CaseOutcome:
    observations: dict[str, object]
    usage: dict[str, object] | None = None


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    kind: CaseKind
    description: str
    run: Callable[[EvalContext], CaseOutcome | None]
    required: bool = True


@dataclass(frozen=True)
class EvalSuite:
    suite_id: str
    cases: tuple[EvalCase, ...]


def _claim_extraction_contract(context: EvalContext) -> CaseOutcome:
    claims = context.provider.extract_claims(_claim_extraction_evidence()).claims

    assert_claim_contract(claims)
    return CaseOutcome(
        observations={
            "claim_count": len(claims),
            "candidate_slugs": sorted({claim.candidate_slug for claim in claims}),
            "source_types": sorted({claim.source_type for claim in claims}),
        }
    )


def _low_trust_risk_contract(context: EvalContext) -> CaseOutcome:
    claims = context.provider.extract_claims(_low_trust_evidence()).claims
    risks = context.provider.cluster_risks(claims).risks

    assert_low_trust_risk_contract(risks)
    return CaseOutcome(
        observations={
            "claim_count": len(claims),
            "risk_count": len(risks),
            "risk_statuses": sorted({risk.status for risk in risks}),
        }
    )


def _verification_contract(context: EvalContext) -> CaseOutcome:
    risk = RiskCluster(
        candidate_slug="crewai",
        risk_key="maintenance",
        title="Maintenance Staleness",
        summary="Community-only claims suggest stale maintenance.",
        severity="medium",
        status="unverified",
        credibility="low",
        supporting_claim_indexes=[0],
    )

    plan = context.provider.plan_verification([risk])
    verdict = context.provider.verify_risk(risk, stronger_evidence=_stronger_evidence())
    verified_risk = risk.model_copy(update={"status": verdict.verdict})
    synthesis = context.provider.synthesize_risks([verified_risk])

    assert_verification_contract(
        tasks=plan.tasks,
        verdict=verdict,
        synthesis=synthesis,
    )
    return CaseOutcome(
        observations={
            "task_count": len(plan.tasks),
            "task_source_types": sorted({task.stronger_source_type for task in plan.tasks}),
            "verdict": verdict.verdict,
            "verdict_source_type": verdict.source_type,
        }
    )


def _workflow_regression_framework_selection(context: EvalContext) -> CaseOutcome:
    return asyncio.run(_run_workflow_regression(context))


async def _run_workflow_regression(context: EvalContext) -> CaseOutcome:
    original_provider_factory = workflow_module._phase3_llm_provider_from_settings
    engine = None
    workflow_module._phase3_llm_provider_from_settings = lambda: context.provider
    try:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async with maker() as db:
            session = await create_decision_session(
                db,
                (
                    "Compare LangGraph, OpenAI Agents SDK, and CrewAI for a Python web "
                    "Agent runtime with checkpointing, human approval gates, observability, "
                    "and maintenance-risk concerns."
                ),
            )
            await append_user_message(
                db,
                session.id,
                (
                    "We need Python, checkpointing, human approval, strong observability, "
                    "and conservative maintenance-risk handling."
                ),
            )
            phase1_run = await create_agent_run(db, session.id)
            session_id = session.id
            phase1_run_id = phase1_run.id

        async with maker() as db:
            await _run_provider_backed_workflow_step(
                run_phase1_workflow(
                    db,
                    phase1_run_id,
                    github_transport=httpx.MockTransport(_workflow_github_handler),
                )
            )

        async with maker() as db:
            source_version = (
                await db.execute(
                    select(DecisionVersion)
                    .where(DecisionVersion.session_id == session_id)
                    .order_by(DecisionVersion.version_number.desc())
                )
            ).scalars().first()
            if source_version is None:
                raise AssertionError("Expected Phase 1 to create a source version.")
            _raise_if_phase3_failure(source_version)

            session = await db.get(DecisionSession, session_id)
            if session is None:
                raise AssertionError("Expected workflow session to exist.")
            decision_context = dict(session.decision_context or {})
            decision_context["phase2_draft"] = {
                "source_version_id": str(source_version.id),
                "candidate_overrides": {},
                "custom_candidates": {},
                "must_include_constraints": {},
                "must_exclude_constraints": {},
                "weight_overrides": {
                    "Observability and traceability": {
                        "weight": 45,
                        "reason": "Observability is the main decision driver.",
                    }
                },
            }
            session.decision_context = decision_context
            session.status = DecisionSessionStatus.queued
            phase2_run, phase2_version = await create_phase2_version_run(db, session_id)
            phase2_run_id = phase2_run.id
            phase2_version_id = phase2_version.id

        async with maker() as db:
            await _run_provider_backed_workflow_step(
                run_phase2_workflow(
                    db,
                    phase2_run_id,
                    github_transport=httpx.MockTransport(_workflow_github_handler),
                )
            )

        async with maker() as db:
            final_workspace = await get_workspace(db, session_id, version_id=phase2_version_id)
            observations = assert_workflow_regression_contract(final_workspace)
            return CaseOutcome(observations=observations)
    finally:
        workflow_module._phase3_llm_provider_from_settings = original_provider_factory
        if engine is not None:
            await engine.dispose()


async def _run_provider_backed_workflow_step(awaitable: Any) -> None:
    try:
        await awaitable
    except RuntimeError as exc:
        provider_failure = _provider_response_cause(exc)
        if provider_failure is not None:
            raise ProviderResponseError(str(exc)) from provider_failure
        raise


def _provider_response_cause(exc: BaseException) -> ProviderResponseError | None:
    current = exc.__cause__
    while current is not None:
        if isinstance(current, ProviderResponseError):
            return current
        current = current.__cause__
    return None


def _raise_if_phase3_failure(version: DecisionVersion) -> None:
    failure = dict(version.gap_analysis or {}).get("phase3_failure")
    if isinstance(failure, dict):
        message = failure.get("error") or "Phase 3 provider failure was hidden by fallback."
        raise ProviderResponseError(str(message))


def _workflow_github_handler(request: httpx.Request) -> httpx.Response:
    repo_name = request.url.path.removeprefix("/repos/")
    repo_payloads = {
        "langchain-ai/langgraph": {
            "stars": 52000,
            "forks": 8700,
            "open_issues": 145,
            "description": "Stateful agent framework with graph orchestration.",
        },
        "openai/openai-agents-python": {
            "stars": 26000,
            "forks": 3100,
            "open_issues": 48,
            "description": "Python Agents SDK for tool-using agent runtimes.",
        },
        "crewAIInc/crewAI": {
            "stars": 33000,
            "forks": 5200,
            "open_issues": 210,
            "description": "Multi-agent orchestration framework for role-based agents.",
        },
    }
    payload = repo_payloads.get(repo_name)
    if payload is None:
        return httpx.Response(404, json={"message": "Not Found"})
    return httpx.Response(
        200,
        json={
            "full_name": repo_name,
            "html_url": f"https://github.com/{repo_name}",
            "description": payload["description"],
            "stargazers_count": payload["stars"],
            "forks_count": payload["forks"],
            "open_issues_count": payload["open_issues"],
            "pushed_at": "2026-05-20T12:00:00Z",
            "license": {"spdx_id": "MIT"},
        },
    )


def _claim_extraction_evidence() -> list[dict[str, Any]]:
    return [
        {
            "candidate_slug": "langgraph",
            "source_type": "official_docs",
            "source_url": "https://docs.langchain.com/langgraph",
            "title": "LangGraph durable execution docs",
            "summary": "Official docs describe durable execution and stateful agent workflows.",
            "citation_text": "Durable execution is documented for agent workflows.",
            "credibility": "high",
        },
        {
            "candidate_slug": "langgraph",
            "source_type": "github_release",
            "source_url": "https://github.com/langchain-ai/langgraph/releases",
            "title": "LangGraph release notes",
            "summary": "Release notes describe checkpoint and runtime updates.",
            "citation_text": "Checkpoint and runtime updates shipped in a release.",
            "credibility": "high",
        },
        {
            "candidate_slug": "crewai",
            "source_type": "hacker_news",
            "source_url": "https://news.ycombinator.com/item?id=000000",
            "title": "Community operational discussion",
            "summary": "Community discussion reports operational and maintenance risk.",
            "citation_text": "Users discuss operational and maintenance risk.",
            "credibility": "low",
        },
    ]


def _low_trust_evidence() -> list[dict[str, Any]]:
    return [
        {
            "candidate_slug": "crewai",
            "source_type": "reddit",
            "source_url": "https://www.reddit.com/r/agents/comments/example",
            "title": "Community-only maintenance report",
            "summary": "Community-only discussion reports stale maintenance risk.",
            "citation_text": "Users report stale maintenance risk.",
            "credibility": "low",
        }
    ]


def _stronger_evidence() -> list[dict[str, Any]]:
    return [
        {
            "candidate_slug": "crewai",
            "source_type": "github_release",
            "source_url": "https://github.com/crewAIInc/crewAI/releases",
            "title": "CrewAI release history",
            "summary": "Release history provides stronger evidence for maintenance review.",
            "citation_text": "Release history is available for maintenance review.",
            "credibility": "high",
        },
        {
            "candidate_slug": "crewai",
            "source_type": "official_docs",
            "source_url": "https://docs.crewai.com/",
            "title": "CrewAI official docs",
            "summary": "Official docs provide stronger evidence for supported behavior.",
            "citation_text": "Official docs are available for supported behavior.",
            "credibility": "high",
        },
    ]


PHASE4_LIVE_SUITE = EvalSuite(
    suite_id="phase4-live",
    cases=(
        EvalCase(
            case_id="deepseek-claim-extraction-contract",
            kind="provider_contract",
            description="DeepSeek claim extraction schema and field preservation contract.",
            run=_claim_extraction_contract,
        ),
        EvalCase(
            case_id="deepseek-low-trust-risk-contract",
            kind="provider_contract",
            description="DeepSeek low-trust community risk policy contract.",
            run=_low_trust_risk_contract,
        ),
        EvalCase(
            case_id="deepseek-verification-contract",
            kind="provider_contract",
            description="DeepSeek verification planning and synthesis contract.",
            run=_verification_contract,
        ),
        EvalCase(
            case_id="workflow-regression-framework-selection",
            kind="workflow_regression",
            description="Backend workflow regression for framework selection.",
            run=_workflow_regression_framework_selection,
        ),
    ),
)

SUITES = {PHASE4_LIVE_SUITE.suite_id: PHASE4_LIVE_SUITE}


def get_suite(suite_id: str) -> EvalSuite:
    try:
        return SUITES[suite_id]
    except KeyError as exc:
        raise ValueError(f"Unknown eval suite: {suite_id}") from exc
