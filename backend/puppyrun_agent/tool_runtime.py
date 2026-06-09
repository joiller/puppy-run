import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_api.config import get_settings
from puppyrun_api.models import ToolCall, utc_now

ToolCallStatus = Literal["completed", "failed", "skipped"]
ToolHandler = Callable[["ToolContext", dict], Awaitable["ToolResult"]]

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
MAX_STRING_LENGTH = 1000


@dataclass(frozen=True)
class ToolResult:
    status: ToolCallStatus
    source_type: str | None = None
    source_url: str | None = None
    request_summary: str | None = None
    response_summary: str | None = None
    payload: dict = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class ToolContext:
    db: AsyncSession
    session_id: UUID
    decision_version_id: UUID | None
    tool_name: str
    idempotency_key: str
    attempt: int


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    handler: ToolHandler


class ToolRuntime:
    def __init__(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        decision_version_id: UUID | None,
        timeout_seconds: float | None = None,
        retry_count: int | None = None,
    ) -> None:
        settings = get_settings()
        self.db = db
        self.session_id = session_id
        self.decision_version_id = decision_version_id
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.tool_timeout_seconds
        )
        self.retry_count = retry_count if retry_count is not None else settings.tool_retry_count
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        self._tools[tool.name] = tool

    async def execute(
        self,
        tool_name: str,
        inputs: dict,
        *,
        idempotency_parts: dict | None = None,
    ) -> ToolResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"tool is not registered: {tool_name}")

        sanitized_inputs = sanitize_payload(inputs)
        idempotency_key = self._idempotency_key(tool_name, inputs, idempotency_parts)
        existing = await self._get_existing_call(idempotency_key)
        if existing is not None:
            return _result_from_call(existing)

        started_at = utc_now()
        call = ToolCall(
            session_id=self.session_id,
            decision_version_id=self.decision_version_id,
            tool_name=tool_name,
            status="running",
            idempotency_key=idempotency_key,
            payload={"inputs": sanitized_inputs},
            started_at=started_at,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(call)
                await self.db.flush()
        except IntegrityError:
            existing = await self._get_existing_call(idempotency_key)
            if existing is not None:
                return _result_from_call(existing)
            raise

        attempts = max(0, self.retry_count) + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            context = ToolContext(
                db=self.db,
                session_id=self.session_id,
                decision_version_id=self.decision_version_id,
                tool_name=tool_name,
                idempotency_key=idempotency_key,
                attempt=attempt,
            )
            try:
                result = await asyncio.wait_for(
                    tool.handler(context, inputs),
                    timeout=self.timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    continue
                return await self._persist_failed_call(
                    call,
                    sanitized_inputs=sanitized_inputs,
                    attempts=attempt,
                    error=exc,
                )

            return await self._persist_result(call, result)

        if last_error is None:
            last_error = RuntimeError("tool failed without an exception")
        return await self._persist_failed_call(
            call,
            sanitized_inputs=sanitized_inputs,
            attempts=attempts,
            error=last_error,
        )

    def _idempotency_key(
        self,
        tool_name: str,
        inputs: dict,
        idempotency_parts: dict | None,
    ) -> str:
        key_payload = {
            "tool_name": tool_name,
            "session_id": str(self.session_id),
            "decision_version_id": (
                str(self.decision_version_id) if self.decision_version_id is not None else None
            ),
            "inputs": hashable_payload(inputs),
            "idempotency_parts": hashable_payload(idempotency_parts or {}),
        }
        return f"{tool_name}:{content_hash(key_payload)}"

    async def _get_existing_call(self, idempotency_key: str) -> ToolCall | None:
        result = await self.db.execute(
            select(ToolCall).where(ToolCall.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()

    async def _persist_result(self, call: ToolCall, result: ToolResult) -> ToolResult:
        source_url = sanitize_text(result.source_url)
        request_summary = sanitize_text(result.request_summary)
        response_summary = sanitize_text(result.response_summary)
        call.status = result.status
        call.source_type = result.source_type
        call.source_url = source_url
        call.request_summary = request_summary
        call.response_summary = response_summary
        call.payload = sanitize_payload(result.payload)
        call.error = sanitize_error(result.error) if result.error else None
        call.completed_at = utc_now()
        await self.db.flush()
        return ToolResult(
            status=result.status,
            source_type=result.source_type,
            source_url=source_url,
            request_summary=request_summary,
            response_summary=response_summary,
            payload=dict(call.payload or {}),
            error=call.error,
        )

    async def _persist_failed_call(
        self,
        call: ToolCall,
        *,
        sanitized_inputs: dict,
        attempts: int,
        error: Exception,
    ) -> ToolResult:
        call.status = "failed"
        call.payload = {"inputs": sanitized_inputs, "attempts": attempts}
        call.error = sanitize_error(str(error))
        call.completed_at = utc_now()
        await self.db.flush()
        return ToolResult(status="failed", payload=dict(call.payload), error=call.error)


def content_hash(value) -> str:
    serialized = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def sanitize_payload(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key] = "[redacted]"
            elif _is_raw_content_key(key_text):
                sanitized[key] = "[redacted raw content]"
            else:
                sanitized[key] = sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _truncate_string(value)
    return value


def hashable_payload(value):
    if isinstance(value, dict):
        hashed = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                hashed[key] = "[redacted]"
            elif _is_raw_content_key(key_text):
                hashed[key] = {"content_hash": content_hash(item)}
            else:
                hashed[key] = hashable_payload(item)
        return hashed
    if isinstance(value, list):
        return [hashable_payload(item) for item in value]
    if isinstance(value, tuple):
        return [hashable_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_inline_secrets(value)
    return value


def sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _truncate_string(value)


def sanitize_error(error: str) -> str:
    sanitized = _redact_inline_secrets(error)
    return _truncate_string(sanitized)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _is_raw_content_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in RAW_CONTENT_KEYS)


def _truncate_string(value: str) -> str:
    redacted = _redact_inline_secrets(value)
    if len(redacted) <= MAX_STRING_LENGTH:
        return redacted
    return f"{redacted[:MAX_STRING_LENGTH]}...[truncated]"


def _redact_inline_secrets(value: str) -> str:
    redacted = re.sub(
        r"(?i)\bauthorization\s*[:=]\s*(bearer\s+)?[^,;&\s]+",
        "Authorization=[redacted]",
        value,
    )
    redacted = re.sub(
        r"(?i)\b([a-z0-9_-]*(?:token|api[_-]?key|password|secret)[a-z0-9_-]*)"
        r"\s*[:=]\s*[^,;&\s]+",
        lambda match: f"{match.group(1)}=[redacted]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\bbearer\s+[^,\s]+",
        "Bearer [redacted]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b(authorization|[a-z0-9_-]*(?:token|api[_-]?key|password|secret)"
        r"[a-z0-9_-]*)\s+[^,\s]+",
        lambda match: f"{match.group(1)} [redacted]",
        redacted,
    )
    return redacted


def _result_from_call(call: ToolCall) -> ToolResult:
    if call.status == "running":
        return ToolResult(
            status="skipped",
            source_type=call.source_type,
            source_url=call.source_url,
            request_summary=call.request_summary,
            response_summary="Duplicate tool call is already running.",
            payload={"reason": "duplicate_call_running"},
        )
    return ToolResult(
        status=call.status,  # type: ignore[arg-type]
        source_type=call.source_type,
        source_url=call.source_url,
        request_summary=call.request_summary,
        response_summary=call.response_summary,
        payload=dict(call.payload or {}),
        error=call.error,
    )
