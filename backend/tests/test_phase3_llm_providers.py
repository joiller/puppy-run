import json

import pytest

from puppyrun_agent.llm_providers import (
    UNSUPPORTED_STRICT_SCHEMA_KEYS,
    DeepSeekLLMProvider,
    DeterministicLLMProvider,
    ExtractedClaim,
    ExtractedClaims,
    OpenAILLMProvider,
    ProviderResponseError,
    ProviderUnavailableError,
    RiskClusters,
    VerificationVerdict,
    _strict_json_schema,
)


def _evidence() -> list[dict]:
    return [
        {
            "candidate_slug": "langgraph",
            "source_type": "official_docs",
            "source_url": "https://docs.example/langgraph",
            "title": "LangGraph docs",
            "summary": "Official docs describe checkpointing and recovery support.",
            "citation_text": "Checkpointing and recovery are documented.",
            "credibility": "high",
        },
        {
            "candidate_slug": "crewai",
            "source_type": "hacker_news",
            "source_url": "https://news.ycombinator.com/item?id=1",
            "title": "CrewAI discussion",
            "summary": "Community discussion reports maintenance risk and stale issues.",
            "citation_text": "Maintenance risk and stale issues.",
            "credibility": "low",
        },
    ]


def test_deterministic_provider_returns_stable_valid_outputs() -> None:
    provider = DeterministicLLMProvider()

    first = provider.extract_claims(_evidence())
    second = provider.extract_claims(_evidence())
    risks = provider.cluster_risks(first.claims)
    plans = provider.plan_verification(risks.risks)
    verdict = provider.verify_risk(
        risks.risks[0],
        stronger_evidence=[
            {
                "source_type": "official_docs",
                "source_url": "https://docs.example/crewai",
                "summary": "Official docs do not mention stale maintenance.",
            }
        ],
    )

    assert first == second
    assert isinstance(first, ExtractedClaims)
    assert isinstance(risks, RiskClusters)
    assert isinstance(verdict, VerificationVerdict)
    assert [claim.candidate_slug for claim in first.claims] == ["langgraph", "crewai"]
    assert risks.risks[0].status == "unverified"
    assert plans.tasks[0].stronger_source_type == "official_docs"
    assert verdict.verdict in {"confirmed", "contradicted", "unresolved"}


def test_deterministic_provider_does_not_confirm_low_trust_community_risk() -> None:
    provider = DeterministicLLMProvider()

    claims = provider.extract_claims([_evidence()[1]])
    risks = provider.cluster_risks(claims.claims)

    assert risks.risks[0].status == "unverified"


class FakeResponses:
    def __init__(
        self,
        output_payload: dict | None = None,
        *,
        response_payload: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.output_payload = output_payload
        self.response_payload = response_payload
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.response_payload is not None:
            return self.response_payload
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(self.output_payload),
                        }
                    ],
                }
            ]
        }


class FakeClient:
    def __init__(
        self,
        output_payload: dict | None = None,
        *,
        response_payload: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = FakeResponses(
            output_payload,
            response_payload=response_payload,
            error=error,
        )


class FakeChatCompletions:
    def __init__(
        self,
        output_payload: dict | None = None,
        *,
        response_payload: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.output_payload = output_payload
        self.response_payload = response_payload
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.response_payload is not None:
            return self.response_payload
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(self.output_payload)},
                }
            ]
        }


class FakeChat:
    def __init__(
        self,
        output_payload: dict | None = None,
        *,
        response_payload: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.completions = FakeChatCompletions(
            output_payload,
            response_payload=response_payload,
            error=error,
        )


class FakeDeepSeekClient:
    def __init__(
        self,
        output_payload: dict | None = None,
        *,
        response_payload: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.chat = FakeChat(
            output_payload,
            response_payload=response_payload,
            error=error,
        )


def test_openai_provider_builds_responses_structured_output_request() -> None:
    client = FakeClient(
        {
            "claims": [
                {
                    "candidate_slug": "langgraph",
                    "source_type": "official_docs",
                    "source_url": "https://docs.example/langgraph",
                    "title": "Docs claim",
                    "summary": "Official docs support checkpointing.",
                    "citation_text": "Checkpointing docs.",
                    "credibility": "high",
                    "confidence": 90,
                }
            ]
        }
    )
    provider = OpenAILLMProvider(
        api_key="test-key",
        model="gpt-5.5",
        client=client,
    )

    claims = provider.extract_claims(_evidence())
    call = client.responses.calls[0]

    assert claims.claims[0].candidate_slug == "langgraph"
    assert call["model"] == "gpt-5.5"
    assert call["store"] is False
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["name"] == "ExtractedClaims"
    schema = call["text"]["format"]["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["claims"]
    claim_schema = schema["$defs"]["ExtractedClaim"]
    assert claim_schema["additionalProperties"] is False
    assert set(claim_schema["required"]) == set(claim_schema["properties"])
    serialized_schema = json.dumps(schema)
    assert "default" not in serialized_schema
    assert "maxLength" not in serialized_schema
    assert "Do not include secrets" in call["instructions"]
    assert call["input"][-1]["role"] == "user"


def test_openai_strict_schema_preserves_business_title_fields() -> None:
    claims_schema = _strict_json_schema(ExtractedClaims)
    risks_schema = _strict_json_schema(RiskClusters)

    claim_schema = claims_schema["$defs"]["ExtractedClaim"]
    risk_schema = risks_schema["$defs"]["RiskCluster"]

    assert "title" in claim_schema["properties"]
    assert "title" in claim_schema["required"]
    assert "title" in risk_schema["properties"]
    assert "title" in risk_schema["required"]
    assert claim_schema["additionalProperties"] is False
    assert risk_schema["additionalProperties"] is False
    assert set(claim_schema["required"]) == set(claim_schema["properties"])
    assert set(risk_schema["required"]) == set(risk_schema["properties"])
    assert not _schema_metadata_keys(claims_schema, UNSUPPORTED_STRICT_SCHEMA_KEYS)
    assert not _schema_metadata_keys(risks_schema, UNSUPPORTED_STRICT_SCHEMA_KEYS)


def test_openai_provider_demotes_confirmed_community_only_risk() -> None:
    client = FakeClient(
        {
            "risks": [
                {
                    "candidate_slug": "crewai",
                    "risk_key": "maintenance",
                    "title": "Maintenance Risk",
                    "summary": "Community discussion reports stale maintenance.",
                    "severity": "medium",
                    "status": "confirmed",
                    "credibility": "high",
                    "supporting_claim_indexes": [],
                }
            ]
        }
    )
    provider = OpenAILLMProvider(api_key="test-key", model="gpt-5.5", client=client)
    low_trust_claim = ExtractedClaim(
        candidate_slug="crewai",
        source_type="hacker_news",
        source_url="https://news.ycombinator.com/item?id=1",
        title="CrewAI discussion",
        summary="Community discussion reports stale maintenance.",
        citation_text="Maintenance risk.",
        credibility="low",
        confidence=55,
        risk_key="maintenance",
    )

    risks = provider.cluster_risks([low_trust_claim])

    assert risks.risks[0].status == "unverified"


def test_openai_provider_converts_refusal_to_sanitized_provider_error() -> None:
    client = FakeClient(
        response_payload={
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "refusal",
                            "refusal": "Cannot process token=secret-token-value.",
                        }
                    ],
                }
            ],
        }
    )
    provider = OpenAILLMProvider(api_key="test-key", model="gpt-5.5", client=client)

    with pytest.raises(ProviderResponseError) as exc_info:
        provider.extract_claims(_evidence())

    message = str(exc_info.value)
    assert "refused" in message
    assert "secret-token-value" not in message
    assert "[redacted]" in message


def test_openai_provider_sanitizes_api_errors() -> None:
    client = FakeClient(error=RuntimeError("upstream api_key=secret-api-key failed"))
    provider = OpenAILLMProvider(api_key="test-key", model="gpt-5.5", client=client)

    with pytest.raises(ProviderResponseError) as exc_info:
        provider.extract_claims(_evidence())

    message = str(exc_info.value)
    assert "OpenAI response request failed" in message
    assert "secret-api-key" not in message
    assert "[redacted]" in message


def test_openai_provider_does_not_run_without_api_key() -> None:
    provider = OpenAILLMProvider(api_key=None, model="gpt-5.5", client=FakeClient({}))

    with pytest.raises(ProviderUnavailableError):
        provider.extract_claims(_evidence())


def test_deepseek_provider_builds_chat_json_output_request() -> None:
    client = FakeDeepSeekClient(
        {
            "claims": [
                {
                    "candidate_slug": "langgraph",
                    "source_type": "official_docs",
                    "source_url": "https://docs.example/langgraph",
                    "title": "Docs claim",
                    "summary": "Official docs support checkpointing.",
                    "citation_text": "Checkpointing docs.",
                    "credibility": "high",
                    "confidence": 90,
                }
            ]
        }
    )
    provider = DeepSeekLLMProvider(
        api_key="test-key",
        model="deepseek-v4-flash",
        client=client,
    )

    claims = provider.extract_claims(_evidence())
    call = client.chat.completions.calls[0]

    assert claims.claims[0].candidate_slug == "langgraph"
    assert call["model"] == "deepseek-v4-flash"
    assert call["stream"] is False
    assert call["response_format"] == {"type": "json_object"}
    assert "JSON" in call["messages"][0]["content"]
    assert "ExtractedClaims" in call["messages"][0]["content"]
    assert "Do not include secrets" in call["messages"][0]["content"]
    assert call["messages"][-1]["role"] == "user"
    assert "evidence_items" in call["messages"][-1]["content"]


def test_deepseek_provider_sanitizes_chat_errors() -> None:
    client = FakeDeepSeekClient(error=RuntimeError("upstream api_key=secret-api-key failed"))
    provider = DeepSeekLLMProvider(
        api_key="test-key",
        model="deepseek-v4-flash",
        client=client,
    )

    with pytest.raises(ProviderResponseError) as exc_info:
        provider.extract_claims(_evidence())

    message = str(exc_info.value)
    assert "DeepSeek chat completion request failed" in message
    assert "secret-api-key" not in message
    assert "[redacted]" in message


def test_deepseek_provider_does_not_run_without_api_key() -> None:
    provider = DeepSeekLLMProvider(
        api_key=None,
        model="deepseek-v4-flash",
        client=FakeDeepSeekClient({}),
    )

    with pytest.raises(ProviderUnavailableError):
        provider.extract_claims(_evidence())


def _schema_metadata_keys(
    value: object,
    unsupported_keys: set[str],
    *,
    in_properties: bool = False,
) -> list[str]:
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            found.extend(_schema_metadata_keys(item, unsupported_keys))
        return found
    if not isinstance(value, dict):
        return []

    found = []
    for key, item in value.items():
        if not in_properties and key in unsupported_keys:
            found.append(key)
        found.extend(
            _schema_metadata_keys(
                item,
                unsupported_keys,
                in_properties=key == "properties",
            )
        )
    return found
