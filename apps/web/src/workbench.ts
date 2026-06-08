import type {
  DecisionVersion,
  EvidenceItem,
  GapAnalysis,
  Phase2Draft,
  Recommendation,
  ScoreCell,
  Workspace
} from "./types";

function versionSortKey(version: DecisionVersion): string {
  return `${String(version.version_number).padStart(6, "0")}:${version.created_at}`;
}

function latestByVersionNumber(versions: DecisionVersion[]): DecisionVersion | null {
  if (versions.length === 0) return null;
  return [...versions].sort((left, right) => versionSortKey(left).localeCompare(versionSortKey(right))).at(-1) ?? null;
}

export function latestVersion(workspace: Workspace): DecisionVersion | null {
  return workspace.active_version ?? latestByVersionNumber(workspace.versions);
}

export function activeRecommendation(workspace: Workspace): Recommendation | null {
  const version = latestVersion(workspace);
  const versionedRecommendations = workspace.recommendations.filter((recommendation) =>
    version ? recommendation.decision_version_id === version.id : recommendation.decision_version_id === null
  );
  return versionedRecommendations.at(-1) ?? workspace.recommendations.at(-1) ?? null;
}

export function scoreCellFor(
  workspace: Workspace,
  candidateId: string,
  criterionId: string
): ScoreCell | null {
  const version = latestVersion(workspace);
  return (
    workspace.score_cells.find(
      (scoreCell) =>
        scoreCell.candidate_id === candidateId &&
        scoreCell.criterion_id === criterionId &&
        (!version || scoreCell.decision_version_id === version.id)
    ) ?? null
  );
}

export function evidenceForScoreCell(workspace: Workspace, scoreCell: ScoreCell): EvidenceItem[] {
  const evidenceById = new Map(workspace.evidence_items.map((item) => [item.id, item]));
  return scoreCell.evidence_item_ids
    .map((evidenceId) => evidenceById.get(evidenceId))
    .filter((item): item is EvidenceItem => Boolean(item));
}

export function hasDraftChanges(draft: Phase2Draft): boolean {
  return (
    Object.keys(draft.candidate_overrides).length > 0 ||
    Object.keys(draft.custom_candidates).length > 0 ||
    Object.keys(draft.must_include_constraints).length > 0 ||
    Object.keys(draft.must_exclude_constraints).length > 0 ||
    Object.keys(draft.weight_overrides).length > 0
  );
}

export function gapSummary(gapAnalysis: GapAnalysis): string {
  const parts: string[] = [];
  if (gapAnalysis.changed_candidates.length > 0) {
    parts.push(`Changed candidates: ${gapAnalysis.changed_candidates.join(", ")}`);
  }
  if (gapAnalysis.changed_constraints.length > 0) {
    parts.push(`Changed constraints: ${gapAnalysis.changed_constraints.join(", ")}`);
  }
  if (gapAnalysis.changed_weights.length > 0) {
    parts.push(`Changed weights: ${gapAnalysis.changed_weights.join(", ")}`);
  }
  if (gapAnalysis.requires_github_fetch) {
    parts.push("GitHub fetch required");
  } else if (gapAnalysis.score_only) {
    parts.push("Score-only rerun");
  } else if (gapAnalysis.requires_research) {
    parts.push("Research required");
  }
  return parts.length > 0 ? parts.join(". ") : "No draft changes yet.";
}
