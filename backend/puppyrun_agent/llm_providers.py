import json
from typing import Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from puppyrun_agent.tool_runtime import sanitize_error

RiskStatus = Literal["confirmed", "contradicted", "unresolved", "unverified"]
RiskSeverity = Literal["low", "medium", "high"]
StructuredModel = TypeVar("StructuredModel", bound=BaseModel)

COMMUNITY_SOURCE_TYPES = {"reddit", "hacker_news", "stack_exchange"}
STRONG_SOURCE_TYPES = {"official_docs", "github_release", "github_issue", "arxiv", "technical_blog"}
UNSUPPORTED_STRICT_SCHEMA_KEYS = {
    "default",
    "title",
    "maxLength",
    "minLength",
    "pattern",
    "format",
    "minimum",
    "maximum",
    "multipleOf",
    "minItems",
    "maxItems",
    "uniqueItems",
    "patternProperties",
    "allOf",
    "not",
    "dependentRequired",
    "dependentSchemas",
    "if",
    "then",
    "else",
}


class ProviderUnavailableError(RuntimeError):
    pass


class ProviderResponseError(RuntimeError):
    pass


class ExtractedClaim(BaseModel):
    candidate_slug: str = Field(min_length=1, max_length=80)
    source_type: str = Field(min_length=1, max_length=80)
    source_url: str = Field(default="", max_length=500)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=800)
    citation_text: str = Field(default="", max_length=500)
    credibility: Literal["low", "medium", "high"]
    confidence: int = Field(ge=0, le=100)
    risk_key: str | None = Field(default=None, max_length=120)


class ExtractedClaims(BaseModel):
    claims: list[ExtractedClaim] = Field(default_factory=list, max_length=50)


class RiskCluster(BaseModel):
    candidate_slug: str = Field(min_length=1, max_length=80)
    risk_key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=800)
    severity: RiskSeverity
    status: RiskStatus
    credibility: Literal["low", "medium", "high"]
    supporting_claim_indexes: list[int] = Field(default_factory=list, max_length=20)


class RiskClusters(BaseModel):
    risks: list[RiskCluster] = Field(default_factory=list, max_length=50)


class VerificationTaskPlan(BaseModel):
    candidate_slug: str = Field(min_length=1, max_length=80)
    risk_key: str = Field(min_length=1, max_length=120)
    verification_question: str = Field(min_length=1, max_length=400)
    stronger_source_type: str = Field(min_length=1, max_length=80)
    stronger_source_url: str | None = Field(default=None, max_length=500)


class VerificationPlan(BaseModel):
    tasks: list[VerificationTaskPlan] = Field(default_factory=list, max_length=50)


class VerificationVerdict(BaseModel):
    verdict: RiskStatus
    rationale: str = Field(min_length=1, max_length=800)
    source_type: str | None = Field(default=None, max_length=80)
    source_url: str | None = Field(default=None, max_length=500)


class RiskSynthesis(BaseModel):
    summary: str = Field(default="", max_length=1200)
    confirmed_risks: list[str] = Field(default_factory=list, max_length=20)
    unresolved_risks: list[str] = Field(default_factory=list, max_length=20)
    contradicted_risks: list[str] = Field(default_factory=list, max_length=20)
    unverified_risks: list[str] = Field(default_factory=list, max_length=20)


class DeterministicLLMProvider:
    def extract_claims(self, evidence_items: list[dict]) -> ExtractedClaims:
        claims = []
        for evidence in evidence_items:
            source_type = str(evidence.get("source_type") or "unknown")
            credibility = _credibility(evidence)
            summary = _clean(evidence.get("summary")) or _clean(evidence.get("citation_text"))
            title = _clean(evidence.get("title")) or source_type
            claims.append(
                ExtractedClaim(
                    candidate_slug=_clean(evidence.get("candidate_slug")) or "unknown",
                    source_type=source_type,
                    source_url=_clean(evidence.get("source_url")),
                    title=title,
                    summary=summary or title,
                    citation_text=_clean(evidence.get("citation_text")) or summary or title,
                    credibility=credibility,
                    confidence=_confidence_for_credibility(credibility),
                    risk_key=_risk_key(summary or title, source_type),
                )
            )
        return ExtractedClaims(claims=claims)

    def cluster_risks(self, claims: list[ExtractedClaim]) -> RiskClusters:
        risks = []
        for index, claim in enumerate(claims):
            risk_key = claim.risk_key or _risk_key(claim.summary, claim.source_type)
            if not risk_key:
                continue
            low_trust = claim.credibility == "low" or claim.source_type in COMMUNITY_SOURCE_TYPES
            risks.append(
                RiskCluster(
                    candidate_slug=claim.candidate_slug,
                    risk_key=risk_key,
                    title=_title_for_risk(risk_key),
                    summary=claim.summary,
                    severity=_severity_for_summary(claim.summary),
                    status="unverified" if low_trust else "unresolved",
                    credibility=claim.credibility,
                    supporting_claim_indexes=[index],
                )
            )
        return RiskClusters(risks=risks)

    def plan_verification(self, risks: list[RiskCluster]) -> VerificationPlan:
        return VerificationPlan(
            tasks=[
                VerificationTaskPlan(
                    candidate_slug=risk.candidate_slug,
                    risk_key=risk.risk_key,
                    verification_question=(
                        f"Find stronger evidence for {risk.candidate_slug} {risk.title}."
                    ),
                    stronger_source_type="official_docs",
                    stronger_source_url=None,
                )
                for risk in risks
            ]
        )

    def verify_risk(
        self,
        risk: RiskCluster,
        *,
        stronger_evidence: list[dict],
    ) -> VerificationVerdict:
        if not stronger_evidence:
            return VerificationVerdict(
                verdict="unresolved",
                rationale="No stronger evidence was available.",
            )
        best = stronger_evidence[0]
        source_type = str(best.get("source_type") or "")
        summary = _clean(best.get("summary"))
        if source_type in STRONG_SOURCE_TYPES and any(
            word in summary.lower() for word in ("not", "fixed", "supported", "documented")
        ):
            verdict: RiskStatus = "contradicted"
        elif source_type in STRONG_SOURCE_TYPES:
            verdict = "confirmed"
        else:
            verdict = "unresolved"
        return VerificationVerdict(
            verdict=verdict,
            rationale=f"Deterministic verdict from {source_type or 'unknown source'}.",
            source_type=source_type or None,
            source_url=_clean(best.get("source_url")) or None,
        )

    def synthesize_risks(self, risks: list[RiskCluster]) -> RiskSynthesis:
        grouped = {
            "confirmed": [],
            "unresolved": [],
            "contradicted": [],
            "unverified": [],
        }
        for risk in risks:
            grouped[risk.status].append(risk.title)
        return RiskSynthesis(
            summary=f"Reviewed {len(risks)} risk signals.",
            confirmed_risks=grouped["confirmed"],
            unresolved_risks=grouped["unresolved"],
            contradicted_risks=grouped["contradicted"],
            unverified_risks=grouped["unverified"],
        )


class OpenAILLMProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str | None = None,
        client=None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.client = client

    def extract_claims(self, evidence_items: list[dict]) -> ExtractedClaims:
        payload = self._create_structured_response(
            schema_model=ExtractedClaims,
            task="Extract concise evidence-backed claims from the supplied source snippets.",
            data={"evidence_items": evidence_items},
        )
        return ExtractedClaims.model_validate(payload)

    def cluster_risks(self, claims: list[ExtractedClaim]) -> RiskClusters:
        risks = self._create_structured_response(
            schema_model=RiskClusters,
            task="Cluster extracted claims into candidate risk signals.",
            data={"claims": [claim.model_dump() for claim in claims]},
        )
        return _enforce_low_trust_risk_policy(risks, claims)

    def plan_verification(self, risks: list[RiskCluster]) -> VerificationPlan:
        payload = self._create_structured_response(
            schema_model=VerificationPlan,
            task="Create verification tasks that target stronger source types.",
            data={"risks": [risk.model_dump() for risk in risks]},
        )
        return VerificationPlan.model_validate(payload)

    def verify_risk(
        self,
        risk: RiskCluster,
        *,
        stronger_evidence: list[dict],
    ) -> VerificationVerdict:
        verdict = self._create_structured_response(
            schema_model=VerificationVerdict,
            task="Return a conservative risk verification verdict.",
            data={"risk": risk.model_dump(), "stronger_evidence": stronger_evidence},
        )
        return _enforce_verdict_source_policy(verdict)

    def synthesize_risks(self, risks: list[RiskCluster]) -> RiskSynthesis:
        payload = self._create_structured_response(
            schema_model=RiskSynthesis,
            task="Summarize verified risk signals for a decision rationale.",
            data={"risks": [risk.model_dump() for risk in risks]},
        )
        return RiskSynthesis.model_validate(payload)

    def _create_structured_response(
        self,
        *,
        schema_model: type[StructuredModel],
        task: str,
        data: dict,
    ) -> StructuredModel:
        if not self.api_key:
            raise ProviderUnavailableError("OpenAI provider requires PUPPYRUN_OPENAI_API_KEY")
        client = self._client()
        try:
            response = client.responses.create(
                model=self.model,
                store=False,
                instructions=(
                    "You extract evidence-grounded Agent framework risk data. "
                    "Do not include secrets, credentials, or raw full community threads. "
                    "Return only data that validates against the requested schema."
                ),
                input=[
                    {"role": "developer", "content": task},
                    {"role": "user", "content": json.dumps(data, sort_keys=True)},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_model.__name__,
                        "strict": True,
                        "schema": _strict_json_schema(schema_model),
                    }
                },
            )
        except Exception as exc:
            raise ProviderResponseError(
                f"OpenAI response request failed: {sanitize_error(str(exc))}"
            ) from exc

        payload = _extract_response_json(response)
        try:
            return schema_model.model_validate(payload)
        except ValidationError as exc:
            raise ProviderResponseError(
                f"OpenAI structured output failed validation: {sanitize_error(str(exc))}"
            ) from exc

    def _client(self):
        if self.client is not None:
            return self.client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderUnavailableError("OpenAI Python SDK is not installed") from exc
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self.client = OpenAI(**kwargs)
        return self.client


def _extract_response_json(response) -> dict:
    status = _response_value(response, "status")
    if status == "incomplete":
        reason = _response_value(_response_value(response, "incomplete_details"), "reason")
        raise ProviderResponseError(
            f"OpenAI response was incomplete: {sanitize_error(str(reason or 'unknown'))}"
        )
    response_error = _response_value(response, "error")
    if response_error:
        raise ProviderResponseError(
            f"OpenAI response returned an error: {sanitize_error(str(response_error))}"
        )
    if isinstance(response, dict):
        output = response.get("output", [])
    else:
        output = getattr(response, "output", [])
    for item in output:
        content = (
            item.get("content", [])
            if isinstance(item, dict)
            else getattr(item, "content", [])
        )
        for part in content:
            refusal = _response_value(part, "refusal")
            if refusal:
                raise ProviderResponseError(
                    f"OpenAI response was refused: {sanitize_error(str(refusal))}"
                )
            part_type = _response_value(part, "type")
            if part_type == "refusal":
                raise ProviderResponseError("OpenAI response was refused.")
            text = _response_value(part, "text")
            if text:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ProviderResponseError(
                        f"OpenAI response JSON was invalid: {sanitize_error(str(exc))}"
                    ) from exc
                if not isinstance(payload, dict):
                    raise ProviderResponseError("OpenAI response JSON was not an object.")
                return payload
    raise ProviderResponseError("OpenAI response did not contain output_text JSON.")


def _response_value(value: object, key: str) -> object:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _strict_json_schema(schema_model: type[BaseModel]) -> dict:
    return _stricten_json_schema(schema_model.model_json_schema())


def _stricten_json_schema(value: object) -> object:
    if isinstance(value, list):
        return [_stricten_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    strict: dict = {}
    for key, item in value.items():
        if key in UNSUPPORTED_STRICT_SCHEMA_KEYS:
            continue
        strict[key] = _stricten_json_schema(item)

    properties = strict.get("properties")
    if isinstance(properties, dict):
        strict["additionalProperties"] = False
        strict["required"] = list(properties.keys())
    return strict


def _enforce_low_trust_risk_policy(
    risk_clusters: RiskClusters,
    claims: list[ExtractedClaim],
) -> RiskClusters:
    guarded = []
    for risk in risk_clusters.risks:
        if risk.status == "confirmed" and _risk_only_has_low_trust_support(risk, claims):
            risk = risk.model_copy(update={"status": "unverified"})
        guarded.append(risk)
    return RiskClusters(risks=guarded)


def _risk_only_has_low_trust_support(risk: RiskCluster, claims: list[ExtractedClaim]) -> bool:
    if risk.credibility == "low":
        return True
    supporting = [
        claims[index]
        for index in risk.supporting_claim_indexes
        if 0 <= index < len(claims)
    ]
    if not supporting:
        same_candidate_claims = [
            claim for claim in claims if claim.candidate_slug == risk.candidate_slug
        ]
        if not same_candidate_claims:
            return False
        supporting = same_candidate_claims
    return all(
        claim.credibility == "low" or claim.source_type in COMMUNITY_SOURCE_TYPES
        for claim in supporting
    )


def _enforce_verdict_source_policy(verdict: VerificationVerdict) -> VerificationVerdict:
    if verdict.verdict != "confirmed":
        return verdict
    if verdict.source_type in STRONG_SOURCE_TYPES:
        return verdict
    return verdict.model_copy(update={"verdict": "unresolved"})


def _clean(value: object) -> str:
    return str(value or "").strip()


def _credibility(evidence: dict) -> Literal["low", "medium", "high"]:
    value = str(evidence.get("credibility") or "").lower()
    if value in {"low", "medium", "high"}:
        return value  # type: ignore[return-value]
    source_type = str(evidence.get("source_type") or "")
    if source_type == "official_docs":
        return "high"
    if source_type in COMMUNITY_SOURCE_TYPES:
        return "low"
    return "medium"


def _confidence_for_credibility(credibility: str) -> int:
    return {"high": 90, "medium": 75, "low": 55}.get(credibility, 50)


def _risk_key(summary: str, source_type: str) -> str | None:
    lowered = summary.lower()
    if "maintenance" in lowered or "stale" in lowered:
        return "maintenance"
    if "risk" in lowered or source_type in COMMUNITY_SOURCE_TYPES:
        return "community_risk"
    return None


def _title_for_risk(risk_key: str) -> str:
    return risk_key.replace("_", " ").title()


def _severity_for_summary(summary: str) -> RiskSeverity:
    lowered = summary.lower()
    if "critical" in lowered or "security" in lowered:
        return "high"
    if "maintenance" in lowered or "stale" in lowered:
        return "medium"
    return "low"
