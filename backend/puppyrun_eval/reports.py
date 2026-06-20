from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from puppyrun_agent.tool_runtime import sanitize_error
from puppyrun_eval.runner import SuiteResult, suite_result_to_dict

REPORT_SCHEMA_VERSION = "phase4-live-report-v1"
SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "token",
    "password",
    "secret",
)
RAW_CONTENT_KEYS = (
    "raw_content",
    "raw_thread",
    "full_thread",
    "full_text",
    "full_body",
    "community_thread",
)
SAFE_USAGE_KEYS = {
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "cost_usd",
    "estimated_cost_usd",
}


@dataclass(frozen=True)
class ReportPaths:
    json_path: Path
    markdown_path: Path


def write_reports(
    suite_result: SuiteResult,
    *,
    report_dir: str | Path | None = None,
) -> ReportPaths:
    destination = Path(report_dir) if report_dir is not None else Path(".eval-reports")
    destination.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC)
    timestamp = generated_at.strftime("%Y%m%d-%H%M%S")
    base_name = f"{suite_result.suite_id}-{timestamp}"
    json_path = destination / f"{base_name}.json"
    markdown_path = destination / f"{base_name}.md"
    payload = report_payload(suite_result, generated_at=generated_at)

    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(_markdown_report(payload))
    return ReportPaths(json_path=json_path, markdown_path=markdown_path)


def report_payload(
    suite_result: SuiteResult,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(UTC)
    payload = suite_result_to_dict(suite_result)
    payload["report_schema_version"] = REPORT_SCHEMA_VERSION
    payload["generated_at"] = generated_at.isoformat()
    payload["summary"] = _summary(payload["case_results"])
    return _sanitize_report_payload(payload)


def _summary(case_results: list[dict[str, Any]]) -> dict[str, int]:
    required = [case for case in case_results if case.get("required")]
    return {
        "case_count": len(case_results),
        "required_count": len(required),
        "pass_count": _status_count(case_results, "pass"),
        "fail_count": _status_count(case_results, "fail"),
        "blocked_count": _status_count(case_results, "blocked"),
        "required_pass_count": _status_count(required, "pass"),
        "required_fail_count": _status_count(required, "fail"),
        "required_blocked_count": _status_count(required, "blocked"),
    }


def _status_count(case_results: list[dict[str, Any]], status: str) -> int:
    return sum(1 for case in case_results if case.get("status") == status)


def _markdown_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        f"# {payload['suite_id']}",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Status: {payload['status']}",
        f"- Provider: {payload['provider_name']}",
        f"- Model: {payload['model_name']}",
        f"- Duration: {float(payload['duration_seconds']):.3f}s",
        (
            "- Cases: "
            f"{summary['pass_count']} pass, "
            f"{summary['fail_count']} fail, "
            f"{summary['blocked_count']} blocked "
            f"({summary['required_count']} required)"
        ),
        "",
        "| Case | Required | Kind | Status | Duration | Failure Category | Message |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in payload["case_results"]:
        lines.append(
            "| "
            f"{_markdown_table_cell(str(case['case_id']))} | "
            f"{_markdown_table_cell('yes' if case.get('required') else 'no')} | "
            f"{_markdown_table_cell(str(case['case_kind']))} | "
            f"{_markdown_table_cell(str(case['status']))} | "
            f"{float(case['duration_seconds']):.3f}s | "
            f"{_markdown_table_cell(str(case.get('failure_category') or ''))} | "
            f"{_markdown_table_cell(str(case.get('failure_message') or ''))} |"
        )
    observation_lines = _observation_lines(payload["case_results"])
    if observation_lines:
        lines.extend(["", "## Observations", "", *observation_lines])
    usage_lines = _usage_lines(payload["case_results"])
    if usage_lines:
        lines.extend(["", "## Usage", "", *usage_lines])
    lines.append("")
    return "\n".join(lines)


def _markdown_table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _observation_lines(case_results: list[dict[str, Any]]) -> list[str]:
    lines = []
    for case in case_results:
        observations = case.get("observations")
        if not observations:
            continue
        lines.append(f"### {_markdown_heading(str(case['case_id']))}")
        for key, value in sorted(observations.items()):
            lines.append(f"- `{key}`: {_markdown_inline(value)}")
        lines.append("")
    return lines[:-1] if lines and lines[-1] == "" else lines


def _usage_lines(case_results: list[dict[str, Any]]) -> list[str]:
    lines = []
    for case in case_results:
        usage = case.get("usage")
        if not usage:
            continue
        lines.append(f"### {_markdown_heading(str(case['case_id']))}")
        for key, value in sorted(usage.items()):
            lines.append(f"- `{key}`: {_markdown_inline(value)}")
        lines.append("")
    return lines[:-1] if lines and lines[-1] == "" else lines


def _markdown_inline(value: object) -> str:
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, sort_keys=True, default=str)
    else:
        rendered = str(value)
    return rendered.replace("\n", " ")


def _markdown_heading(value: str) -> str:
    return value.replace("\n", " ").strip()


def _sanitize_report_payload(value: Any, *, key: str = "", in_usage: bool = False) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        next_in_usage = in_usage or key == "usage"
        for item_key, item in value.items():
            item_key_text = str(item_key)
            if _is_sensitive_key(item_key_text, in_usage=next_in_usage):
                sanitized[item_key] = "[redacted]"
            elif _is_raw_content_key(item_key_text):
                sanitized[item_key] = "[redacted raw content]"
            else:
                sanitized[item_key] = _sanitize_report_payload(
                    item,
                    key=item_key_text,
                    in_usage=next_in_usage,
                )
        return sanitized
    if isinstance(value, list):
        return [_sanitize_report_payload(item, key=key, in_usage=in_usage) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_report_payload(item, key=key, in_usage=in_usage) for item in value]
    if isinstance(value, str):
        return sanitize_error(value)
    return value


def _is_sensitive_key(key: str, *, in_usage: bool) -> bool:
    normalized = key.lower().replace("-", "_")
    if in_usage and normalized in SAFE_USAGE_KEYS:
        return False
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _is_raw_content_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in RAW_CONTENT_KEYS)
