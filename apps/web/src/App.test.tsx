import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { createSession, getWorkspace, listSessions, sendMessage, startRun } from "./api";
import type { DecisionSession, StartAgentRunResponse, Workspace } from "./types";

vi.mock("./api", () => ({
  createSession: vi.fn(),
  getWorkspace: vi.fn(),
  listSessions: vi.fn(),
  sendMessage: vi.fn(),
  startRun: vi.fn()
}));

const createSessionMock = vi.mocked(createSession);
const getWorkspaceMock = vi.mocked(getWorkspace);
const listSessionsMock = vi.mocked(listSessions);
const sendMessageMock = vi.mocked(sendMessage);
const startRunMock = vi.mocked(startRun);
let triggerPoll: (() => void) | null = null;

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

function makeSession(
  status: DecisionSession["status"],
  currentSummary: string | null = null
): DecisionSession {
  return {
    id: "session-1",
    title: "Compare LangGraph",
    prompt: "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime.",
    status,
    workflow_stage: status === "created" ? "clarifying" : status,
    decision_context: { domain: "agent_framework_selection" },
    current_summary: currentSummary,
    created_at: "2026-05-27T00:00:00Z",
    updated_at: "2026-05-27T00:00:00Z"
  };
}

function makeRunResponse(session: DecisionSession): StartAgentRunResponse {
  return {
    session,
    run: {
      id: "run-1",
      session_id: session.id,
      status: session.status,
      job_id: "job-1",
      created_at: "2026-05-22T00:00:00Z",
      updated_at: "2026-05-22T00:00:00Z"
    }
  };
}

async function flushAsyncUpdates() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function runPoll() {
  if (!triggerPoll) {
    throw new Error("Polling callback was not registered");
  }
  act(() => {
    triggerPoll?.();
  });
  await flushAsyncUpdates();
}

function getRunButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Run Phase 1 Agent" }) as HTMLButtonElement;
}

function makeWorkspace(
  session: DecisionSession,
  extraMessages: Array<{ role: string; content: string }> = []
): Workspace {
  return {
    session,
    messages: [
      {
        id: "message-1",
        session_id: session.id,
        role: "assistant",
        content:
          "Which constraints matter most for this Agent runtime: checkpointing, human approval, Python or TypeScript fit, deployment simplicity, and observability?",
        created_at: "2026-05-27T00:00:00Z"
      },
      ...extraMessages.map((message, index) => ({
        id: `message-extra-${index}`,
        session_id: session.id,
        role: message.role,
        content: message.content,
        created_at: "2026-05-27T00:00:00Z"
      }))
    ],
    candidates: [],
    criteria: [],
    evidence_items: [],
    recommendations: [],
    events: []
  };
}

function makeCompletedWorkspace(session: DecisionSession): Workspace {
  return {
    ...makeWorkspace(session),
    candidates: [
      {
        id: "candidate-1",
        session_id: session.id,
        name: "LangGraph",
        slug: "langgraph",
        repo_full_name: "langchain-ai/langgraph",
        include_reason: "Included for checkpointed stateful workflows.",
        health_summary: "langchain-ai/langgraph: 50000 stars.",
        health_metrics: { stars: 50000 },
        score: 85,
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    criteria: [
      {
        id: "criterion-1",
        session_id: session.id,
        name: "Runtime control and state",
        weight: 30,
        rationale: "State handling is central for long-running Agent workflows.",
        evidence_needed: "Checkpoint and state support.",
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    evidence_items: [
      {
        id: "evidence-1",
        session_id: session.id,
        candidate_id: "candidate-1",
        criterion_id: null,
        source_type: "github_repo",
        source_url: "https://github.com/langchain-ai/langgraph",
        title: "GitHub repository health for LangGraph",
        summary: "langchain-ai/langgraph: 50000 stars.",
        credibility: "medium",
        payload: { stars: 50000 },
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    recommendations: [
      {
        id: "recommendation-1",
        session_id: session.id,
        recommended_candidate_id: "candidate-1",
        summary: session.current_summary ?? "",
        rationale: { recommended_slug: "langgraph" },
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    events: [
      {
        id: "event-1",
        run_id: "run-1",
        event_type: "recommendation_generated",
        message: session.current_summary ?? "",
        payload: {},
        created_at: "2026-05-27T00:00:00Z"
      }
    ]
  };
}

describe("App", () => {
  beforeEach(() => {
    triggerPoll = null;
    vi.spyOn(window, "setInterval").mockImplementation((handler: TimerHandler, timeout?: number) => {
      if (timeout === 2000 && typeof handler === "function") {
        triggerPoll = handler as () => void;
      }
      return 1;
    });
    vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);
    createSessionMock.mockReset();
    getWorkspaceMock.mockReset();
    listSessionsMock.mockReset();
    sendMessageMock.mockReset();
    startRunMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows clarification, recommendation, evidence, and trace for a Phase 1 run", async () => {
    const created = makeSession("created");
    const ready: DecisionSession = { ...created, workflow_stage: "ready_for_research" };
    const completed: DecisionSession = {
      ...created,
      status: "completed",
      workflow_stage: "completed",
      current_summary: "Recommended: LangGraph. It scored 85/100."
    };
    let workspace = makeWorkspace(created);

    listSessionsMock.mockImplementation(async () => [workspace.session]);
    createSessionMock.mockImplementation(async () => {
      workspace = makeWorkspace(created);
      return created;
    });
    getWorkspaceMock.mockImplementation(async () => workspace);
    sendMessageMock.mockImplementation(async () => {
      workspace = makeWorkspace(ready, [{ role: "user", content: "We need checkpointing." }]);
      return workspace;
    });
    startRunMock.mockImplementation(async () => {
      return makeRunResponse({ ...ready, status: "queued" });
    });

    render(<App />);
    await flushAsyncUpdates();

    fireEvent.click(screen.getByRole("button", { name: "Create session" }));
    await waitFor(() => {
      expect(screen.getByText(/constraints matter most/i)).toBeTruthy();
    });
    expect(getRunButton().disabled).toBe(true);

    const clarificationContent =
      "We need Python, checkpointing, human approval, and observability.";
    fireEvent.change(screen.getByLabelText("Clarification answer"), {
      target: { value: clarificationContent }
    });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    await waitFor(() => {
      expect(within(screen.getByLabelText("Decision workspace")).getByText("ready_for_research"))
        .toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "Compare LangGraph ready_for_research" }))
      .toBeTruthy();
    expect(sendMessageMock).toHaveBeenCalledWith(created.id, clarificationContent);
    expect(getRunButton().disabled).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Run Phase 1 Agent" }));
    await waitFor(() => {
      expect(startRunMock).toHaveBeenCalledWith(created.id);
    });
    workspace = makeCompletedWorkspace(completed);
    await runPoll();

    await waitFor(() => {
      expect(within(screen.getByLabelText("Decision workspace")).getByText(/Recommended: LangGraph/))
        .toBeTruthy();
      expect(screen.getByText("GitHub repository health for LangGraph")).toBeTruthy();
      const traceRow = within(screen.getByLabelText("Evidence and trace"))
        .getByText("recommendation_generated")
        .closest("article");
      expect(traceRow).toBeTruthy();
      expect(
        within(traceRow as HTMLElement).getByText("Recommended: LangGraph. It scored 85/100.")
      ).toBeTruthy();
      expect(getRunButton().disabled).toBe(true);
      expect(screen.getByRole("button", { name: "Compare LangGraph completed" })).toBeTruthy();
    });
  });

  it("keeps the latest selected workspace when workspace requests resolve out of order", async () => {
    const firstSession: DecisionSession = {
      ...makeSession("created"),
      id: "session-1",
      title: "Compare LangGraph",
      workflow_stage: "clarifying"
    };
    const secondSession: DecisionSession = {
      ...makeSession("created"),
      id: "session-2",
      title: "Compare CrewAI",
      workflow_stage: "clarifying"
    };
    const firstRequest = deferred<Workspace>();
    const secondRequest = deferred<Workspace>();

    listSessionsMock.mockImplementation(async () => [firstSession, secondSession]);
    getWorkspaceMock.mockImplementation(async (sessionId: string) => {
      if (sessionId === firstSession.id) return firstRequest.promise;
      if (sessionId === secondSession.id) return secondRequest.promise;
      throw new Error(`Unexpected workspace request: ${sessionId}`);
    });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
      expect(screen.getByRole("button", { name: /Compare CrewAI/ })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    fireEvent.click(screen.getByRole("button", { name: /Compare CrewAI/ }));

    await act(async () => {
      secondRequest.resolve(
        makeWorkspace(secondSession, [{ role: "assistant", content: "Second workspace marker" }])
      );
      await secondRequest.promise;
    });
    await waitFor(() => {
      expect(screen.getByText("Second workspace marker")).toBeTruthy();
    });

    await act(async () => {
      firstRequest.resolve(
        makeWorkspace(firstSession, [{ role: "assistant", content: "First workspace marker" }])
      );
      await firstRequest.promise;
    });

    await waitFor(() => {
      expect(screen.getByText("Second workspace marker")).toBeTruthy();
      expect(screen.queryByText("First workspace marker")).toBeNull();
    });
  });

  it("ignores stale clarification responses after switching sessions", async () => {
    const firstSession: DecisionSession = {
      ...makeSession("created"),
      id: "session-1",
      title: "Compare LangGraph",
      workflow_stage: "clarifying"
    };
    const secondSession: DecisionSession = {
      ...makeSession("created"),
      id: "session-2",
      title: "Compare CrewAI",
      workflow_stage: "clarifying"
    };
    const firstReady: DecisionSession = {
      ...firstSession,
      workflow_stage: "ready_for_research"
    };
    const answerRequest = deferred<Workspace>();

    listSessionsMock.mockImplementation(async () => [firstSession, secondSession]);
    getWorkspaceMock.mockImplementation(async (sessionId: string) => {
      if (sessionId === firstSession.id) {
        return makeWorkspace(firstSession, [{ role: "assistant", content: "First workspace marker" }]);
      }
      if (sessionId === secondSession.id) {
        return makeWorkspace(secondSession, [
          { role: "assistant", content: "Second workspace marker" }
        ]);
      }
      throw new Error(`Unexpected workspace request: ${sessionId}`);
    });
    sendMessageMock.mockImplementation(async () => answerRequest.promise);

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
      expect(screen.getByRole("button", { name: /Compare CrewAI/ })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(screen.getByText("First workspace marker")).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("Clarification answer"), {
      target: { value: "Python and checkpointing matter most." }
    });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    fireEvent.click(screen.getByRole("button", { name: /Compare CrewAI/ }));
    await waitFor(() => {
      expect(screen.getByText("Second workspace marker")).toBeTruthy();
    });

    await act(async () => {
      answerRequest.resolve(
        makeWorkspace(firstReady, [{ role: "assistant", content: "First answered marker" }])
      );
      await answerRequest.promise;
    });

    await waitFor(() => {
      expect(screen.getByText("Second workspace marker")).toBeTruthy();
      expect(screen.queryByText("First answered marker")).toBeNull();
    });
  });

  it("ignores stale run responses after switching sessions", async () => {
    const firstReady: DecisionSession = {
      ...makeSession("created"),
      id: "session-1",
      title: "Compare LangGraph",
      workflow_stage: "ready_for_research"
    };
    const secondReady: DecisionSession = {
      ...makeSession("created"),
      id: "session-2",
      title: "Compare CrewAI",
      workflow_stage: "ready_for_research"
    };
    const runRequest = deferred<StartAgentRunResponse>();

    listSessionsMock.mockImplementation(async () => [firstReady, secondReady]);
    getWorkspaceMock.mockImplementation(async (sessionId: string) => {
      if (sessionId === firstReady.id) {
        return makeWorkspace(firstReady, [
          { role: "assistant", content: "First run workspace marker" }
        ]);
      }
      if (sessionId === secondReady.id) {
        return makeWorkspace(secondReady, [
          { role: "assistant", content: "Second run workspace marker" }
        ]);
      }
      throw new Error(`Unexpected workspace request: ${sessionId}`);
    });
    startRunMock.mockImplementation(async () => runRequest.promise);

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
      expect(screen.getByRole("button", { name: /Compare CrewAI/ })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(screen.getByText("First run workspace marker")).toBeTruthy();
      expect(getRunButton().disabled).toBe(false);
    });

    fireEvent.click(screen.getByRole("button", { name: "Run Phase 1 Agent" }));
    fireEvent.click(screen.getByRole("button", { name: /Compare CrewAI/ }));
    await waitFor(() => {
      expect(screen.getByText("Second run workspace marker")).toBeTruthy();
    });

    await act(async () => {
      runRequest.resolve(makeRunResponse({ ...firstReady, status: "queued" }));
      await runRequest.promise;
    });

    await waitFor(() => {
      expect(screen.getByText("Second run workspace marker")).toBeTruthy();
      expect(screen.queryByText("First run workspace marker")).toBeNull();
    });
  });
});
