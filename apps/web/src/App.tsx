import { FormEvent, useEffect, useRef, useState } from "react";
import { createSession, getWorkspace, listSessions, sendMessage, startRun } from "./api";
import type { DecisionSession, Workspace } from "./types";
import "./App.css";

const samplePrompt =
  "I want to build an Agent decision platform. Should I use LangGraph, OpenAI Agents SDK, CrewAI, AutoGen, or build a small runtime myself?";

const nonRunnableRunStatuses = new Set<DecisionSession["status"]>([
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled"
]);

export default function App() {
  const [prompt, setPrompt] = useState(samplePrompt);
  const [sessions, setSessions] = useState<DecisionSession[]>([]);
  const [selected, setSelected] = useState<DecisionSession | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [clarificationAnswer, setClarificationAnswer] = useState("");
  const selectedIdRef = useRef<string | null>(null);
  const workspaceRequestIdRef = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  function selectSession(session: DecisionSession | null) {
    selectedIdRef.current = session?.id ?? null;
    setSelected(session);
  }

  function applyWorkspace(nextWorkspace: Workspace) {
    setWorkspace(nextWorkspace);
    selectSession(nextWorkspace.session);
    setSessions((currentSessions) => {
      const existingIndex = currentSessions.findIndex((session) => session.id === nextWorkspace.session.id);
      if (existingIndex === -1) {
        return [...currentSessions, nextWorkspace.session];
      }
      return currentSessions.map((session) =>
        session.id === nextWorkspace.session.id ? nextWorkspace.session : session
      );
    });
  }

  function isCurrentActionResponse(actionSessionId: string, responseSessionId: string) {
    return selectedIdRef.current === actionSessionId && responseSessionId === actionSessionId;
  }

  function invalidateWorkspaceReads() {
    workspaceRequestIdRef.current += 1;
  }

  async function loadWorkspace(session: DecisionSession) {
    selectSession(session);
    const requestId = ++workspaceRequestIdRef.current;
    const nextWorkspace = await getWorkspace(session.id);
    if (
      requestId !== workspaceRequestIdRef.current ||
      selectedIdRef.current !== session.id ||
      nextWorkspace.session.id !== session.id
    ) {
      return;
    }
    applyWorkspace(nextWorkspace);
  }

  async function refreshSessions(selectedId = selectedIdRef.current) {
    const items = await listSessions();
    setSessions(items);
    if (!selectedId || selectedIdRef.current !== selectedId) {
      return;
    }
    const current = items.find((item) => item.id === selectedId);
    if (current) {
      selectSession(current);
      const requestId = ++workspaceRequestIdRef.current;
      const nextWorkspace = await getWorkspace(current.id);
      if (
        requestId !== workspaceRequestIdRef.current ||
        selectedIdRef.current !== current.id ||
        nextWorkspace.session.id !== current.id
      ) {
        return;
      }
      applyWorkspace(nextWorkspace);
    }
  }

  useEffect(() => {
    refreshSessions().catch((err: unknown) => setError(String(err)));
    const timer = window.setInterval(() => {
      refreshSessions().catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, []);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setIsBusy(true);
    setError(null);
    try {
      const created = await createSession(prompt);
      selectSession(created);
      await loadWorkspace(created);
      await refreshSessions(created.id);
    } catch (err) {
      setError(String(err));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleRun() {
    if (!selected || !canRun) return;
    const actionSessionId = selected.id;
    invalidateWorkspaceReads();
    setIsBusy(true);
    setError(null);
    try {
      const result = await startRun(actionSessionId);
      if (!isCurrentActionResponse(actionSessionId, result.session.id)) {
        return;
      }
      selectSession(result.session);
      setWorkspace((current) =>
        current?.session.id === result.session.id ? { ...current, session: result.session } : current
      );
      await refreshSessions(result.session.id);
    } catch (err) {
      setError(String(err));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleAnswer(event: FormEvent) {
    event.preventDefault();
    if (!selected || clarificationAnswer.trim().length < 2) return;
    const actionSessionId = selected.id;
    invalidateWorkspaceReads();
    setIsBusy(true);
    setError(null);
    try {
      const nextWorkspace = await sendMessage(actionSessionId, clarificationAnswer);
      if (!isCurrentActionResponse(actionSessionId, nextWorkspace.session.id)) {
        return;
      }
      applyWorkspace(nextWorkspace);
      setClarificationAnswer("");
    } catch (err) {
      setError(String(err));
    } finally {
      setIsBusy(false);
    }
  }

  const recommendationSummary =
    workspace?.recommendations.at(-1)?.summary ?? selected?.current_summary ?? null;
  const canRun =
    !!selected &&
    workspace?.session.id === selected.id &&
    workspace.session.workflow_stage === "ready_for_research" &&
    !nonRunnableRunStatuses.has(workspace.session.status);

  return (
    <main className="app-shell">
      <header className="top-bar">
        <div>
          <p className="eyebrow">PuppyRun Phase 1</p>
          <h1>Decision workbench</h1>
        </div>
        <span className="top-status">{selected?.status ?? "idle"}</span>
      </header>

      <section className="workspace-grid">
        <section className="session-column" aria-label="Sessions">
          <form className="composer" onSubmit={handleCreate}>
            <label htmlFor="prompt">Decision prompt</label>
            <textarea
              id="prompt"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
            />
            <button disabled={isBusy || prompt.trim().length < 10} type="submit">
              Create session
            </button>
            {error && <p className="error">{error}</p>}
          </form>
          <div className="session-list">
            {sessions.map((session) => (
              <button
                aria-label={`${session.title} ${session.workflow_stage}`}
                className={selected?.id === session.id ? "session selected" : "session"}
                key={session.id}
                onClick={() => loadWorkspace(session).catch((err: unknown) => setError(String(err)))}
                type="button"
              >
                <span>{session.title}</span>
                <strong>{session.workflow_stage}</strong>
              </button>
            ))}
          </div>
        </section>

        <section className="decision-column" aria-label="Decision workspace">
          <div className="stage-bar">
            <span>{workspace?.session.workflow_stage ?? "no_session"}</span>
            <button disabled={!canRun || isBusy} onClick={handleRun} type="button">
              Run Phase 1 Agent
            </button>
          </div>

          <section className="clarification-thread">
            <h2>Clarification</h2>
            {workspace?.messages.map((message) => (
              <article className={`message ${message.role}`} key={message.id}>
                <strong>{message.role}</strong>
                <p>{message.content}</p>
              </article>
            ))}
            <form onSubmit={handleAnswer}>
              <label htmlFor="clarification-answer">Clarification answer</label>
              <textarea
                id="clarification-answer"
                value={clarificationAnswer}
                onChange={(event) => setClarificationAnswer(event.target.value)}
              />
              <button
                disabled={!selected || isBusy || clarificationAnswer.trim().length < 2}
                type="submit"
              >
                Send answer
              </button>
            </form>
          </section>

          <section className="recommendation-section">
            <h2>Recommendation</h2>
            <p>{recommendationSummary ?? "No recommendation yet."}</p>
          </section>
        </section>

        <aside className="evidence-column" aria-label="Evidence and trace">
          <h2>Candidates</h2>
          {workspace?.candidates.map((candidate) => (
            <article className="candidate-row" key={candidate.id}>
              <strong>{candidate.name}</strong>
              <span>{candidate.score ?? "-"} / 100</span>
              <p>{candidate.health_summary}</p>
            </article>
          ))}

          <h2>Criteria</h2>
          {workspace?.criteria.map((criterion) => (
            <article className="criterion-row" key={criterion.id}>
              <strong>{criterion.name}</strong>
              <span>{criterion.weight}</span>
              <p>{criterion.rationale}</p>
            </article>
          ))}

          <h2>Evidence</h2>
          {workspace?.evidence_items.map((item) => (
            <article className="evidence-row" key={item.id}>
              <a href={item.source_url} target="_blank" rel="noreferrer">
                {item.title}
              </a>
              <p>{item.summary}</p>
            </article>
          ))}

          <h2>Trace</h2>
          {workspace?.events.map((event) => (
            <article className="trace-row" key={event.id}>
              <strong>{event.event_type}</strong>
              <p>{event.message}</p>
            </article>
          ))}
        </aside>
      </section>
    </main>
  );
}
