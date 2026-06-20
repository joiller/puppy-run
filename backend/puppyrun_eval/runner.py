from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from time import perf_counter
from typing import Any

from puppyrun_agent.llm_providers import (
    DeepSeekLLMProvider,
    ProviderResponseError,
    ProviderUnavailableError,
)
from puppyrun_agent.tool_runtime import sanitize_error
from puppyrun_api.config import Settings
from puppyrun_eval.cases import CaseOutcome, EvalCase, EvalContext, EvalProvider, get_suite


class ResultStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


class FailureCategory(StrEnum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_RESPONSE = "provider_response"
    QUALITY_REGRESSION = "quality_regression"
    RUNNER_ERROR = "runner_error"


@dataclass(frozen=True)
class CaseResult:
    suite_id: str
    case_id: str
    case_kind: str
    required: bool
    status: ResultStatus
    failure_category: FailureCategory | None
    failure_message: str | None
    provider_name: str
    model_name: str
    duration_seconds: float
    started_at: str
    finished_at: str
    usage: dict[str, Any] | None = None
    observations: dict[str, Any] | None = None


@dataclass(frozen=True)
class SuiteResult:
    suite_id: str
    status: ResultStatus
    case_results: list[CaseResult]
    provider_name: str
    model_name: str
    duration_seconds: float
    started_at: str
    finished_at: str
    report_paths: dict[str, str] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        if self.status == ResultStatus.PASS:
            return 0
        if self.status == ResultStatus.BLOCKED:
            return 2
        return 1


def sanitize_failure_message(message: str | None) -> str | None:
    if message is None:
        return None
    return sanitize_error(message)


def run_suite(
    suite_id: str,
    *,
    settings: Settings | None = None,
    provider: EvalProvider | None = None,
) -> SuiteResult:
    suite_started = _utc_now()
    suite_timer = perf_counter()
    settings = settings or Settings()
    suite = get_suite(suite_id)
    provider_name = settings.llm_provider
    model_name = _model_name(settings)

    if suite.suite_id == "phase4-live" and provider is None and provider_name != "deepseek":
        case_results = [
            _blocked_case_result(
                suite.suite_id,
                case,
                provider_name=provider_name,
                model_name=model_name,
                message="phase4-live requires PUPPYRUN_LLM_PROVIDER=deepseek",
            )
            for case in suite.cases
        ]
    elif suite.suite_id == "phase4-live" and provider is None and not settings.deepseek_api_key:
        case_results = [
            _blocked_case_result(
                suite.suite_id,
                case,
                provider_name=provider_name,
                model_name=model_name,
                message="DeepSeek provider requires PUPPYRUN_DEEPSEEK_API_KEY",
            )
            for case in suite.cases
        ]
    else:
        if suite.suite_id == "phase4-live" and provider is None:
            provider = DeepSeekLLMProvider(
                api_key=settings.deepseek_api_key,
                model=settings.deepseek_model,
                base_url=settings.deepseek_base_url,
            )
        context = EvalContext(provider=provider)
        case_results = [
            _run_case(
                suite.suite_id,
                case,
                context=context,
                provider_name=provider_name,
                model_name=model_name,
            )
            for case in suite.cases
        ]

    suite_finished = _utc_now()
    return SuiteResult(
        suite_id=suite.suite_id,
        status=_suite_status(case_results),
        case_results=case_results,
        provider_name=provider_name,
        model_name=model_name,
        duration_seconds=round(perf_counter() - suite_timer, 6),
        started_at=suite_started,
        finished_at=suite_finished,
    )


def suite_result_to_dict(result: SuiteResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["status"] = result.status.value
    for case in payload["case_results"]:
        case["status"] = case["status"].value
        if case["failure_category"] is not None:
            case["failure_category"] = case["failure_category"].value
    return payload


def _run_case(
    suite_id: str,
    case: EvalCase,
    *,
    context: EvalContext,
    provider_name: str,
    model_name: str,
) -> CaseResult:
    started_at = _utc_now()
    timer = perf_counter()
    outcome = None
    try:
        outcome = case.run(context)
    except ProviderUnavailableError as exc:
        return _case_result(
            suite_id,
            case,
            status=ResultStatus.BLOCKED,
            failure_category=FailureCategory.PROVIDER_UNAVAILABLE,
            failure_message=str(exc),
            provider_name=provider_name,
            model_name=model_name,
            started_at=started_at,
            timer=timer,
        )
    except ProviderResponseError as exc:
        return _case_result(
            suite_id,
            case,
            status=ResultStatus.FAIL,
            failure_category=FailureCategory.PROVIDER_RESPONSE,
            failure_message=str(exc),
            provider_name=provider_name,
            model_name=model_name,
            started_at=started_at,
            timer=timer,
        )
    except AssertionError as exc:
        return _case_result(
            suite_id,
            case,
            status=ResultStatus.FAIL,
            failure_category=FailureCategory.QUALITY_REGRESSION,
            failure_message=str(exc),
            provider_name=provider_name,
            model_name=model_name,
            started_at=started_at,
            timer=timer,
        )
    except NotImplementedError as exc:
        return _case_result(
            suite_id,
            case,
            status=ResultStatus.BLOCKED,
            failure_category=FailureCategory.RUNNER_ERROR,
            failure_message=str(exc),
            provider_name=provider_name,
            model_name=model_name,
            started_at=started_at,
            timer=timer,
        )
    except Exception as exc:
        return _case_result(
            suite_id,
            case,
            status=ResultStatus.FAIL,
            failure_category=FailureCategory.RUNNER_ERROR,
            failure_message=str(exc),
            provider_name=provider_name,
            model_name=model_name,
            started_at=started_at,
            timer=timer,
        )
    return _case_result(
        suite_id,
        case,
        status=ResultStatus.PASS,
        failure_category=None,
        failure_message=None,
        provider_name=provider_name,
        model_name=model_name,
        started_at=started_at,
        timer=timer,
        outcome=outcome,
    )


def _blocked_case_result(
    suite_id: str,
    case: EvalCase,
    *,
    provider_name: str,
    model_name: str,
    message: str,
) -> CaseResult:
    started_at = _utc_now()
    return CaseResult(
        suite_id=suite_id,
        case_id=case.case_id,
        case_kind=case.kind,
        required=case.required,
        status=ResultStatus.BLOCKED,
        failure_category=FailureCategory.PROVIDER_UNAVAILABLE,
        failure_message=sanitize_failure_message(message),
        provider_name=provider_name,
        model_name=model_name,
        duration_seconds=0.0,
        started_at=started_at,
        finished_at=started_at,
        usage=None,
        observations=None,
    )


def _case_result(
    suite_id: str,
    case: EvalCase,
    *,
    status: ResultStatus,
    failure_category: FailureCategory | None,
    failure_message: str | None,
    provider_name: str,
    model_name: str,
    started_at: str,
    timer: float,
    outcome: CaseOutcome | None = None,
) -> CaseResult:
    return CaseResult(
        suite_id=suite_id,
        case_id=case.case_id,
        case_kind=case.kind,
        required=case.required,
        status=status,
        failure_category=failure_category,
        failure_message=sanitize_failure_message(failure_message),
        provider_name=provider_name,
        model_name=model_name,
        duration_seconds=round(perf_counter() - timer, 6),
        started_at=started_at,
        finished_at=_utc_now(),
        usage=outcome.usage if outcome else None,
        observations=outcome.observations if outcome else None,
    )


def _suite_status(case_results: list[CaseResult]) -> ResultStatus:
    required_results = [result for result in case_results if result.required]
    if any(result.status == ResultStatus.FAIL for result in required_results):
        return ResultStatus.FAIL
    if any(result.status == ResultStatus.BLOCKED for result in required_results):
        return ResultStatus.BLOCKED
    return ResultStatus.PASS


def _model_name(settings: Settings) -> str:
    if settings.llm_provider == "deepseek":
        return settings.deepseek_model
    if settings.llm_provider == "openai":
        return settings.openai_model
    return "deterministic"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
