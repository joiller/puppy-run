import type { DecisionSession, StartAgentRunResponse } from "./types";

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

export async function startRun(sessionId: string): Promise<StartAgentRunResponse> {
  return request<StartAgentRunResponse>(`/api/v1/sessions/${sessionId}/runs`, {
    method: "POST"
  });
}
