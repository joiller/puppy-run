export type DecisionSessionStatus =
  | "created"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

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

export interface DecisionCandidate {
  id: string;
  session_id: string;
  name: string;
  slug: string;
  repo_full_name: string;
  include_reason: string;
  health_summary: string | null;
  health_metrics: Record<string, unknown>;
  score: number | null;
  created_at: string;
}

export interface DecisionCriterion {
  id: string;
  session_id: string;
  name: string;
  weight: number;
  rationale: string;
  evidence_needed: string;
  created_at: string;
}

export interface EvidenceItem {
  id: string;
  session_id: string;
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
  recommended_candidate_id: string | null;
  summary: string;
  rationale: Record<string, unknown>;
  created_at: string;
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
  candidates: DecisionCandidate[];
  criteria: DecisionCriterion[];
  evidence_items: EvidenceItem[];
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
