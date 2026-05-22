import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { createSession, listSessions, startRun } from "./api";
import type { DecisionSession, StartAgentRunResponse } from "./types";

vi.mock("./api", () => ({
  createSession: vi.fn(),
  listSessions: vi.fn(),
  startRun: vi.fn()
}));

const createSessionMock = vi.mocked(createSession);
const listSessionsMock = vi.mocked(listSessions);
const startRunMock = vi.mocked(startRun);
let triggerPoll: (() => void) | null = null;

function makeSession(
  status: DecisionSession["status"],
  currentSummary: string | null = null
): DecisionSession {
  return {
    id: "session-1",
    title: "Compare LangGraph",
    prompt: "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime.",
    status,
    current_summary: currentSummary,
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z"
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

function getDetailPanel(): HTMLElement {
  const panel = screen.getByRole("heading", { name: "Run status" }).closest("section");
  if (!panel) {
    throw new Error("Run status panel was not found");
  }
  return panel;
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
    listSessionsMock.mockReset();
    startRunMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("keeps the selected session detail in sync with polling results", async () => {
    const created = makeSession("created");
    const queued = makeSession("queued");
    const completed = makeSession(
      "completed",
      "Phase 0 dummy Agent completed. Real research workflow is not enabled yet."
    );
    let serverSessions: DecisionSession[] = [];

    listSessionsMock.mockImplementation(async () => serverSessions);
    createSessionMock.mockImplementation(async () => {
      serverSessions = [created];
      return created;
    });
    startRunMock.mockImplementation(async () => {
      serverSessions = [queued];
      return makeRunResponse(queued);
    });

    render(<App />);
    await flushAsyncUpdates();

    fireEvent.click(screen.getByRole("button", { name: "Create session" }));
    await waitFor(() => {
      expect(within(getDetailPanel()).getByText("created")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Start dummy Agent run" }));
    await waitFor(() => {
      expect(within(getDetailPanel()).getByText("queued")).toBeTruthy();
    });

    serverSessions = [completed];
    await runPoll();

    await waitFor(() => {
      const detailText = getDetailPanel().textContent ?? "";
      expect(detailText).toContain("completed");
      expect(detailText).toContain(completed.current_summary);
    });
  });
});
