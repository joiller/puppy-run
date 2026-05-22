import { FormEvent, useEffect, useState } from "react";
import { createSession, listSessions, startRun } from "./api";
import type { DecisionSession } from "./types";
import "./App.css";

const samplePrompt =
  "I want to build an Agent decision platform. Should I use LangGraph, OpenAI Agents SDK, CrewAI, AutoGen, or build a small runtime myself?";

export default function App() {
  const [prompt, setPrompt] = useState(samplePrompt);
  const [sessions, setSessions] = useState<DecisionSession[]>([]);
  const [selected, setSelected] = useState<DecisionSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  async function refreshSessions() {
    const items = await listSessions();
    setSessions(items);
    if (selected) {
      setSelected(items.find((item) => item.id === selected.id) ?? selected);
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
      setSelected(created);
      await refreshSessions();
    } catch (err) {
      setError(String(err));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleRun() {
    if (!selected) return;
    setIsBusy(true);
    setError(null);
    try {
      const result = await startRun(selected.id);
      setSelected(result.session);
      await refreshSessions();
    } catch (err) {
      setError(String(err));
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <section className="hero">
        <div>
          <p className="eyebrow">PuppyRun Phase 0</p>
          <h1>Agent decision workbench skeleton</h1>
          <p>
            Create a decision session, enqueue a dummy Agent run, and watch the backend update
            session state through the worker.
          </p>
        </div>
      </section>

      <section className="workspace">
        <form className="panel composer" onSubmit={handleCreate}>
          <label htmlFor="prompt">Decision prompt</label>
          <textarea id="prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} />
          <button disabled={isBusy || prompt.trim().length < 10} type="submit">
            Create session
          </button>
          {error && <p className="error">{error}</p>}
        </form>

        <section className="panel">
          <div className="panel-header">
            <h2>Sessions</h2>
            <button type="button" onClick={() => refreshSessions()} disabled={isBusy}>
              Refresh
            </button>
          </div>
          <div className="session-list">
            {sessions.map((session) => (
              <button
                className={selected?.id === session.id ? "session selected" : "session"}
                key={session.id}
                onClick={() => setSelected(session)}
                type="button"
              >
                <span>{session.title}</span>
                <strong>{session.status}</strong>
              </button>
            ))}
          </div>
        </section>

        <section className="panel detail">
          <div className="panel-header">
            <h2>Run status</h2>
            <button disabled={!selected || isBusy} onClick={handleRun} type="button">
              Start dummy Agent run
            </button>
          </div>
          {selected ? (
            <div>
              <p className="status">{selected.status}</p>
              <p>{selected.prompt}</p>
              {selected.current_summary && <p className="summary">{selected.current_summary}</p>}
            </div>
          ) : (
            <p>Select or create a session.</p>
          )}
        </section>
      </section>
    </main>
  );
}
