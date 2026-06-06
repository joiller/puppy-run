from dataclasses import dataclass


@dataclass(frozen=True)
class CriterionProfile:
    name: str
    weight: int
    rationale: str
    evidence_needed: str
    is_locked: bool = False
    phase2_weight_reason: str = ""


def generate_criteria(context: dict) -> list[CriterionProfile]:
    constraints = set(context.get("constraints", []))
    state_weight = 30 if {"checkpointing", "stateful_runtime"} & constraints else 25
    observability_weight = 20 if "observability" in constraints else 15
    human_weight = 20 if "human_in_loop" in constraints else 15
    ergonomics_weight = 20
    health_weight = 100 - state_weight - observability_weight - human_weight - ergonomics_weight
    return [
        CriterionProfile(
            name="Runtime control and state",
            weight=state_weight,
            rationale="State handling is central for long-running Agent workflows.",
            evidence_needed=(
                "Repository docs and implementation signals for graph state, checkpoints, "
                "and resumes."
            ),
        ),
        CriterionProfile(
            name="Human-in-the-loop fit",
            weight=human_weight,
            rationale=(
                "The target workflow needs safe review points before expensive or risky actions."
            ),
            evidence_needed="Signals for approvals, interrupts, handoffs, or review checkpoints.",
        ),
        CriterionProfile(
            name="Observability and traceability",
            weight=observability_weight,
            rationale="PuppyRun needs inspectable Agent traces and auditable decisions.",
            evidence_needed="Tracing, event, logging, or run inspection support.",
        ),
        CriterionProfile(
            name="Developer ergonomics",
            weight=ergonomics_weight,
            rationale=(
                "The first version should be buildable by a small team without heavy "
                "framework lock-in."
            ),
            evidence_needed="SDK simplicity, Python fit, examples, and integration surface.",
        ),
        CriterionProfile(
            name="Open-source project health",
            weight=health_weight,
            rationale="The chosen framework should show active maintenance and adoption signals.",
            evidence_needed=(
                "GitHub stars, forks, open issues, recent push date, license, "
                "and repository metadata."
            ),
        ),
    ]


def apply_weight_overrides(
    criteria: list[CriterionProfile],
    overrides: dict,
) -> list[CriterionProfile]:
    adjusted = []
    for criterion in criteria:
        override = overrides.get(criterion.name)
        if not isinstance(override, dict):
            adjusted.append(criterion)
            continue

        adjusted.append(
            CriterionProfile(
                name=criterion.name,
                weight=int(override.get("weight", criterion.weight)),
                rationale=criterion.rationale,
                evidence_needed=criterion.evidence_needed,
                is_locked=True,
                phase2_weight_reason=_clean_text(override.get("reason")),
            )
        )
    return adjusted


def _clean_text(value: object) -> str:
    return str(value).strip() if value is not None else ""
