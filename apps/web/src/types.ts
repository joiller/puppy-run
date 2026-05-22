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
  current_summary: string | null;
  created_at: string;
  updated_at: string;
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
