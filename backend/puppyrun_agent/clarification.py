from puppyrun_agent.catalog import CANDIDATE_REGISTRY

KEYWORD_TO_CONSTRAINT = {
    "checkpoint": "checkpointing",
    "state": "stateful_runtime",
    "human": "human_in_loop",
    "approval": "human_in_loop",
    "trace": "observability",
    "tracing": "observability",
    "observability": "observability",
    "python": "python",
    "typescript": "typescript",
}


def build_initial_context(prompt: str) -> dict:
    constraints = _detect_constraints(prompt)
    return {
        "domain": "agent_framework_selection",
        "mentioned_candidates": _detect_mentioned_candidates(prompt),
        "constraints": sorted(constraints),
        "language_preference": _detect_language_preference(prompt, constraints),
        "clarification_turns": 0,
    }


def build_initial_question(context: dict) -> str:
    return (
        "Which constraints matter most for this Agent runtime: checkpointing, "
        "human approval, Python or TypeScript fit, deployment simplicity, and observability?"
    )


def update_context_with_answer(context: dict, answer: str) -> dict:
    detected_constraints = _detect_constraints(answer)
    merged_constraints = set(context.get("constraints", [])) | detected_constraints
    updated = dict(context)
    updated["constraints"] = sorted(merged_constraints)
    updated["language_preference"] = _detect_language_preference(
        answer,
        detected_constraints,
        fallback=context.get("language_preference"),
    )
    updated["clarification_turns"] = int(context.get("clarification_turns", 0)) + 1
    return updated


def _detect_mentioned_candidates(prompt: str) -> list[str]:
    lowered = prompt.lower()
    matches: list[tuple[int, int, str]] = []
    for registry_index, candidate in enumerate(CANDIDATE_REGISTRY):
        aliases = (candidate.name.lower(), candidate.slug.replace("_", " "))
        positions = [lowered.find(alias) for alias in aliases if lowered.find(alias) >= 0]
        if positions:
            matches.append((min(positions), registry_index, candidate.slug))
    return [slug for _, _, slug in sorted(matches)]


def _detect_constraints(text: str) -> set[str]:
    lowered = text.lower()
    return {
        constraint
        for keyword, constraint in KEYWORD_TO_CONSTRAINT.items()
        if keyword in lowered
    }


def _detect_language_preference(
    text: str,
    constraints: set[str],
    fallback: str | None = None,
) -> str:
    lowered = text.lower()
    language_positions = {
        language: lowered.find(language)
        for language in ("python", "typescript")
        if language in constraints and lowered.find(language) >= 0
    }
    if language_positions:
        return min(language_positions.items(), key=lambda item: item[1])[0]
    if fallback in {"python", "typescript"}:
        return fallback
    return "typescript" if "typescript" in constraints else "python"
