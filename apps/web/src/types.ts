export type DecisionSessionStatus =
  | "created"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type DecisionVersionStatus = "queued" | "running" | "completed" | "failed";

export type CandidateOverrideAction =
  | "include"
  | "exclude"
  | "must_include"
  | "must_exclude"
  | "lock";

export interface DecisionSession {
  id: string;
  title: string;
  prompt: string;
  status: DecisionSessionStatus;
  workflow_stage: string;
  decision_context: Record<string, unknown>;
  current_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface DecisionMessage {
  id: string;
  session_id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface DecisionVersion {
  id: string;
  session_id: string;
  version_number: number;
  label: string;
  status: DecisionVersionStatus;
  source_version_id: string | null;
  change_summary: Record<string, unknown>;
  gap_analysis: Record<string, unknown>;
  adr: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface DecisionCandidate {
  id: string;
  session_id: string;
  decision_version_id: string | null;
  name: string;
  slug: string;
  repo_full_name: string;
  include_reason: string;
  health_summary: string | null;
  health_metrics: Record<string, unknown>;
  score: number | null;
  selection_state: string;
  is_locked: boolean;
  created_at: string;
}

export interface DecisionCriterion {
  id: string;
  session_id: string;
  decision_version_id: string | null;
  name: string;
  weight: number;
  rationale: string;
  evidence_needed: string;
  is_locked: boolean;
  created_at: string;
}

export interface EvidenceItem {
  id: string;
  session_id: string;
  decision_version_id: string | null;
  candidate_id: string | null;
  criterion_id: string | null;
  source_type: string;
  source_url: string;
  title: string;
  summary: string;
  credibility: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Recommendation {
  id: string;
  session_id: string;
  decision_version_id: string | null;
  recommended_candidate_id: string | null;
  summary: string;
  rationale: Record<string, unknown>;
  created_at: string;
}

export interface ScoreCell {
  id: string;
  session_id: string;
  decision_version_id: string;
  candidate_id: string;
  criterion_id: string;
  score: number;
  status: string;
  explanation: string;
  evidence_item_ids: string[];
  created_at: string;
}

export interface CandidateOverride {
  action: CandidateOverrideAction;
  reason: string;
}

export interface CustomCandidateDraft {
  name: string;
  slug: string;
  repo_full_name: string;
  reason: string;
}

export interface ConstraintOverride {
  enabled: boolean;
  reason: string;
}

export interface WeightOverride {
  weight: number;
  reason: string;
}

export interface Phase2Draft {
  source_version_id: string | null;
  candidate_overrides: Record<string, CandidateOverride>;
  custom_candidates: Record<string, CustomCandidateDraft>;
  must_include_constraints: Record<string, ConstraintOverride>;
  must_exclude_constraints: Record<string, ConstraintOverride>;
  weight_overrides: Record<string, WeightOverride>;
}

export interface GapAnalysis {
  requires_research: boolean;
  requires_github_fetch: boolean;
  score_only: boolean;
  changed_candidates: string[];
  changed_constraints: string[];
  changed_weights: string[];
  research_tasks: Array<Record<string, unknown>>;
  reuse_tasks: Array<Record<string, unknown>>;
  items: Array<Record<string, unknown>>;
}

export interface AgentEvent {
  id: string;
  run_id: string;
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Workspace {
  session: DecisionSession;
  messages: DecisionMessage[];
  versions: DecisionVersion[];
  active_version: DecisionVersion | null;
  draft: Phase2Draft;
  gap_analysis: GapAnalysis;
  candidates: DecisionCandidate[];
  criteria: DecisionCriterion[];
  evidence_items: EvidenceItem[];
  score_cells: ScoreCell[];
  recommendations: Recommendation[];
  events: AgentEvent[];
}

export interface StartAgentRunResponse {
  session: DecisionSession;
  run: {
    id: string;
    session_id: string;
    status: string;
    job_id: string | null;
    created_at: string;
    updated_at: string;
  };
}
