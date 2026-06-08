import type { DecisionSession, Phase2Draft, StartAgentRunResponse, Workspace } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function listSessions(): Promise<DecisionSession[]> {
  return request<DecisionSession[]>("/api/v1/sessions");
}

export async function createSession(prompt: string): Promise<DecisionSession> {
  return request<DecisionSession>("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ prompt })
  });
}

export async function getWorkspace(sessionId: string, versionId?: string): Promise<Workspace> {
  const query = versionId ? `?version_id=${encodeURIComponent(versionId)}` : "";
  return request<Workspace>(`/api/v1/sessions/${sessionId}/workspace${query}`);
}

export async function sendMessage(sessionId: string, content: string): Promise<Workspace> {
  return request<Workspace>(`/api/v1/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content })
  });
}

export async function updateDraft(sessionId: string, draft: Phase2Draft): Promise<Workspace> {
  return request<Workspace>(`/api/v1/sessions/${sessionId}/draft`, {
    method: "PATCH",
    body: JSON.stringify(draft)
  });
}

export async function startRun(sessionId: string): Promise<StartAgentRunResponse> {
  return request<StartAgentRunResponse>(`/api/v1/sessions/${sessionId}/runs`, {
    method: "POST"
  });
}

export async function createDecisionVersion(sessionId: string): Promise<StartAgentRunResponse> {
  return request<StartAgentRunResponse>(`/api/v1/sessions/${sessionId}/versions`, {
    method: "POST"
  });
}
