import type {
  Claim,
  DecisionVersion,
  EvidenceItem,
  GapAnalysis,
  Phase2Draft,
  Recommendation,
  RiskSignal,
  ScoreCell,
  ToolCall,
  VerificationTask,
  Workspace
} from "./types";

export interface ToolCallGroup {
  key: string;
  status: string;
  source_type: string;
  items: ToolCall[];
}

export interface RiskSummaryCounts {
  total: number;
  confirmed: number;
  contradicted: number;
  unresolved: number;
  unverified: number;
}

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

function matchesActiveVersion(decisionVersionId: string | null, version: DecisionVersion | null): boolean {
  return version ? decisionVersionId === version.id : decisionVersionId === null;
}

function rowSortKey(row: { created_at: string; id: string }): string {
  return `${row.created_at}:${row.id}`;
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

export function activeRiskSignals(workspace: Workspace): RiskSignal[] {
  const version = latestVersion(workspace);
  return workspace.risk_signals
    .filter((risk) => matchesActiveVersion(risk.decision_version_id, version))
    .sort((left, right) => rowSortKey(left).localeCompare(rowSortKey(right)));
}

export function activeClaims(workspace: Workspace): Claim[] {
  const version = latestVersion(workspace);
  return workspace.claims
    .filter((claim) => matchesActiveVersion(claim.decision_version_id, version))
    .sort((left, right) => rowSortKey(left).localeCompare(rowSortKey(right)));
}

export function activeVerificationTasks(workspace: Workspace): VerificationTask[] {
  const version = latestVersion(workspace);
  return workspace.verification_tasks
    .filter((task) => matchesActiveVersion(task.decision_version_id, version))
    .sort((left, right) => rowSortKey(left).localeCompare(rowSortKey(right)));
}

export function activeToolCalls(workspace: Workspace): ToolCall[] {
  const version = latestVersion(workspace);
  return workspace.tool_calls
    .filter((toolCall) => matchesActiveVersion(toolCall.decision_version_id, version))
    .sort((left, right) => rowSortKey(left).localeCompare(rowSortKey(right)));
}

export function claimsSupportingRisk(workspace: Workspace, risk: RiskSignal): Claim[] {
  const claimIds = new Set(risk.supporting_claim_ids);
  return activeClaims(workspace).filter((claim) => claimIds.has(claim.id));
}

export function verificationTasksForRisk(workspace: Workspace, risk: RiskSignal): VerificationTask[] {
  const taskIds = new Set(risk.verification_task_ids);
  return activeVerificationTasks(workspace).filter(
    (task) => taskIds.has(task.id) || task.risk_signal_id === risk.id
  );
}

export function evidenceForClaim(workspace: Workspace, claim: Claim): EvidenceItem | null {
  if (!claim.source_evidence_item_id) return null;
  return (
    workspace.evidence_items.find((item) => item.id === claim.source_evidence_item_id) ??
    null
  );
}

export function riskSummaryCounts(risks: RiskSignal[]): RiskSummaryCounts {
  const counts: RiskSummaryCounts = {
    total: risks.length,
    confirmed: 0,
    contradicted: 0,
    unresolved: 0,
    unverified: 0
  };
  for (const risk of risks) {
    if (risk.status === "confirmed") counts.confirmed += 1;
    if (risk.status === "contradicted") counts.contradicted += 1;
    if (risk.status === "unresolved") counts.unresolved += 1;
    if (risk.status === "unverified") counts.unverified += 1;
  }
  return counts;
}

export function sourceTypesForWorkspace(workspace: Workspace): string[] {
  const sourceTypes = new Set<string>();
  for (const item of workspace.evidence_items) {
    if (item.source_type) sourceTypes.add(item.source_type);
  }
  for (const claim of activeClaims(workspace)) {
    if (claim.source_type) sourceTypes.add(claim.source_type);
  }
  for (const toolCall of activeToolCalls(workspace)) {
    if (toolCall.source_type) sourceTypes.add(toolCall.source_type);
  }
  return Array.from(sourceTypes).sort();
}

export function toolCallsGroupedByStatusAndSource(
  workspace: Workspace,
  sourceType: string | null = null
): ToolCallGroup[] {
  const groups = new Map<string, ToolCallGroup>();
  for (const toolCall of activeToolCalls(workspace)) {
    const source = toolCall.source_type ?? "internal";
    if (sourceType && source !== sourceType) continue;
    const key = `${toolCall.status}:${source}`;
    const group = groups.get(key) ?? {
      key,
      status: toolCall.status,
      source_type: source,
      items: []
    };
    group.items.push(toolCall);
    groups.set(key, group);
  }
  return Array.from(groups.values()).sort((left, right) => left.key.localeCompare(right.key));
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
