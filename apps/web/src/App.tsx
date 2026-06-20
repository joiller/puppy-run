import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  createDecisionVersion,
  createSession,
  getDemoStatus,
  getWorkspace,
  listSessions,
  sendMessage,
  setDemoLiveEnabled,
  startRun,
  updateDraft
} from "./api";
import type {
  CandidateOverrideAction,
  DecisionCandidate,
  DecisionCriterion,
  DecisionSession,
  DemoSafetyStatus,
  Phase2Draft,
  Workspace
} from "./types";
import {
  activeClaims,
  activeRecommendation,
  activeRiskSignals,
  activeVerificationTasks,
  claimsSupportingRisk,
  evidenceForClaim,
  evidenceForScoreCell,
  gapSummary,
  hasDraftChanges,
  latestVersion,
  riskSummaryCounts,
  sourceTypesForWorkspace,
  toolCallsGroupedByStatusAndSource,
  verificationTasksForRisk,
  scoreCellFor
} from "./workbench";
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

const candidateActions: Array<{ action: CandidateOverrideAction; label: string }> = [
  { action: "include", label: "Include" },
  { action: "exclude", label: "Exclude" },
  { action: "must_include", label: "Must include" },
  { action: "must_exclude", label: "Must exclude" },
  { action: "lock", label: "Lock" }
];

const emptyCustomCandidate = {
  name: "",
  slug: "",
  repo_full_name: "",
  reason: ""
};

function cloneDraft(draft: Phase2Draft): Phase2Draft {
  return {
    source_version_id: draft.source_version_id,
    candidate_overrides: { ...draft.candidate_overrides },
    custom_candidates: { ...draft.custom_candidates },
    must_include_constraints: { ...draft.must_include_constraints },
    must_exclude_constraints: { ...draft.must_exclude_constraints },
    weight_overrides: { ...draft.weight_overrides }
  };
}

function draftSourceVersion(workspace: Workspace, selectedVersionId: string | null): string | null {
  return workspace.draft.source_version_id ?? selectedVersionId ?? latestVersion(workspace)?.id ?? null;
}

function draftForEdit(workspace: Workspace, selectedVersionId: string | null): Phase2Draft {
  const draft = cloneDraft(workspace.draft);
  draft.source_version_id = draftSourceVersion(workspace, selectedVersionId);
  return draft;
}

function knownConstraints(workspace: Workspace): string[] {
  const rawConstraints = workspace.session.decision_context.constraints;
  const contextConstraints = Array.isArray(rawConstraints)
    ? rawConstraints.filter((value): value is string => typeof value === "string")
    : [];
  return Array.from(
    new Set([
      ...contextConstraints,
      ...Object.keys(workspace.draft.must_include_constraints),
      ...Object.keys(workspace.draft.must_exclude_constraints)
    ])
  );
}

function formatList(items: string[]): string {
  return items.length > 0 ? items.join(", ") : "none";
}

function clampWeight(rawWeight: string, fallback: number): number {
  const parsed = Number(rawWeight);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(100, Math.max(0, Math.round(parsed)));
}

function weightDraftsForWorkspace(workspace: Workspace): Record<string, string> {
  return Object.fromEntries(
    workspace.criteria.map((criterion) => [
      criterion.name,
      String(workspace.draft.weight_overrides[criterion.name]?.weight ?? criterion.weight)
    ])
  );
}

function sourceMatches(item: { source_type: string | null }, sourceType: string | null): boolean {
  return !sourceType || item.source_type === sourceType;
}

function versionMatches(item: { decision_version_id: string | null }, versionId: string | null): boolean {
  return versionId ? item.decision_version_id === versionId : item.decision_version_id === null;
}

function scoreImpactLabel(scoreImpact: number): string {
  if (scoreImpact === 0) return "0";
  return scoreImpact > 0 ? `+${scoreImpact}` : String(scoreImpact);
}

function sourceTypeLabel(sourceType: string | null): string {
  return sourceType ?? "internal";
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  if (typeof err === "object" && err !== null && "message" in err) {
    const message = (err as { message?: unknown }).message;
    if (typeof message === "string") {
      return message;
    }
  }
  return String(err);
}

export default function App() {
  const [prompt, setPrompt] = useState(samplePrompt);
  const [sessions, setSessions] = useState<DecisionSession[]>([]);
  const [selected, setSelected] = useState<DecisionSession | null>(null);
  const [selectedVersionId, setSelectedVersionIdState] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [clarificationAnswer, setClarificationAnswer] = useState("");
  const [customCandidate, setCustomCandidate] = useState(emptyCustomCandidate);
  const [weightDrafts, setWeightDrafts] = useState<Record<string, string>>({});
  const [selectedScoreCellId, setSelectedScoreCellId] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  const [selectedRiskId, setSelectedRiskId] = useState<string | null>(null);
  const [sourceTypeFilter, setSourceTypeFilter] = useState<string | null>(null);
  const selectedIdRef = useRef<string | null>(null);
  const selectedVersionIdRef = useRef<string | null>(null);
  const workspaceRequestIdRef = useRef(0);
  const pendingWorkspaceLoadRequestIdRef = useRef<number | null>(null);
  const draftRequestIdRef = useRef(0);
  const dirtyWeightNamesRef = useRef<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [isDraftBusy, setIsDraftBusy] = useState(false);
  const [isVersionBusy, setIsVersionBusy] = useState(false);
  const [adminToken, setAdminToken] = useState(
    () => window.localStorage.getItem("puppyrun-admin-token") ?? ""
  );
  const [adminStatus, setAdminStatus] = useState<DemoSafetyStatus | null>(null);
  const [adminError, setAdminError] = useState<string | null>(null);
  const [isAdminBusy, setIsAdminBusy] = useState(false);
  const isAdminRoute = window.location.pathname === "/admin";

  function setSelectedVersionId(versionId: string | null) {
    selectedVersionIdRef.current = versionId;
    setSelectedVersionIdState(versionId);
  }

  function selectSession(session: DecisionSession | null) {
    selectedIdRef.current = session?.id ?? null;
    setSelected(session);
  }

  function applyWorkspace(nextWorkspace: Workspace, requestedVersionId: string | null = null) {
    const nextActiveVersion = nextWorkspace.active_version ?? latestVersion(nextWorkspace);
    const nextSelectedVersionId =
      requestedVersionId ??
      (selectedVersionIdRef.current === null && nextWorkspace.session.status !== "completed"
        ? null
        : nextActiveVersion?.id ?? null);
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
    setSelectedVersionId(nextSelectedVersionId);
    setWeightDrafts((currentDrafts) => {
      const nextDrafts = weightDraftsForWorkspace(nextWorkspace);
      for (const weightName of dirtyWeightNamesRef.current) {
        if (Object.hasOwn(nextDrafts, weightName) && currentDrafts[weightName] !== undefined) {
          nextDrafts[weightName] = currentDrafts[weightName];
        }
      }
      return nextDrafts;
    });
    setSelectedScoreCellId((currentCellId) =>
      currentCellId && nextWorkspace.score_cells.some((scoreCell) => scoreCell.id === currentCellId)
        ? currentCellId
        : null
    );
    setSelectedEvidenceId((currentEvidenceId) =>
      currentEvidenceId &&
      nextWorkspace.evidence_items.some((evidenceItem) => evidenceItem.id === currentEvidenceId)
        ? currentEvidenceId
        : null
    );
    setSelectedRiskId((currentRiskId) =>
      currentRiskId && nextWorkspace.risk_signals.some((risk) => risk.id === currentRiskId)
        ? currentRiskId
        : null
    );
    setSourceTypeFilter((currentSourceType) =>
      currentSourceType && sourceTypesForWorkspace(nextWorkspace).includes(currentSourceType)
        ? currentSourceType
        : null
    );
  }

  function isCurrentActionResponse(
    actionSessionId: string,
    responseSessionId: string,
    actionVersionId = selectedVersionIdRef.current
  ) {
    return (
      selectedIdRef.current === actionSessionId &&
      responseSessionId === actionSessionId &&
      selectedVersionIdRef.current === actionVersionId
    );
  }

  function invalidateWorkspaceReads() {
    workspaceRequestIdRef.current += 1;
  }

  async function loadWorkspace(session: DecisionSession, versionId: string | null = null) {
    selectSession(session);
    const requestId = ++workspaceRequestIdRef.current;
    pendingWorkspaceLoadRequestIdRef.current = requestId;
    try {
      const nextWorkspace = await getWorkspace(session.id, versionId ?? undefined);
      if (
        requestId !== workspaceRequestIdRef.current ||
        selectedIdRef.current !== session.id ||
        nextWorkspace.session.id !== session.id
      ) {
        return;
      }
      applyWorkspace(nextWorkspace, versionId);
    } finally {
      if (pendingWorkspaceLoadRequestIdRef.current === requestId) {
        pendingWorkspaceLoadRequestIdRef.current = null;
      }
    }
  }

  async function refreshSessions(
    selectedId = selectedIdRef.current,
    versionId = selectedVersionIdRef.current
  ) {
    const items = await listSessions();
    setSessions(items);
    if (pendingWorkspaceLoadRequestIdRef.current !== null) {
      return;
    }
    if (!selectedId || selectedIdRef.current !== selectedId || selectedVersionIdRef.current !== versionId) {
      return;
    }
    const current = items.find((item) => item.id === selectedId);
    if (current) {
      selectSession(current);
      const requestId = ++workspaceRequestIdRef.current;
      const nextWorkspace = await getWorkspace(current.id, versionId ?? undefined);
      if (
        requestId !== workspaceRequestIdRef.current ||
        selectedIdRef.current !== current.id ||
        selectedVersionIdRef.current !== versionId ||
        nextWorkspace.session.id !== current.id
      ) {
        return;
      }
      applyWorkspace(nextWorkspace, versionId);
    }
  }

  useEffect(() => {
    if (isAdminRoute) {
      return undefined;
    }
    refreshSessions().catch((err: unknown) => setError(errorMessage(err)));
    const timer = window.setInterval(() => {
      refreshSessions().catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [isAdminRoute]);

  async function loadAdminStatus() {
    setIsAdminBusy(true);
    setAdminError(null);
    try {
      window.localStorage.setItem("puppyrun-admin-token", adminToken);
      setAdminStatus(await getDemoStatus(adminToken));
    } catch (err) {
      setAdminError(errorMessage(err));
    } finally {
      setIsAdminBusy(false);
    }
  }

  async function handleAdminToggle(enabled: boolean) {
    setIsAdminBusy(true);
    setAdminError(null);
    try {
      window.localStorage.setItem("puppyrun-admin-token", adminToken);
      setAdminStatus(await setDemoLiveEnabled(adminToken, enabled));
    } catch (err) {
      setAdminError(errorMessage(err));
    } finally {
      setIsAdminBusy(false);
    }
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setIsBusy(true);
    setError(null);
    try {
      const created = await createSession(prompt);
      selectSession(created);
      setSelectedVersionId(null);
      await loadWorkspace(created);
      await refreshSessions(created.id, selectedVersionIdRef.current);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleRun() {
    if (!selected || !canRun) return;
    const actionSessionId = selected.id;
    const actionVersionId = selectedVersionIdRef.current;
    invalidateWorkspaceReads();
    setIsBusy(true);
    setError(null);
    try {
      const result = await startRun(actionSessionId);
      if (!isCurrentActionResponse(actionSessionId, result.session.id, actionVersionId)) {
        return;
      }
      selectSession(result.session);
      setWorkspace((current) =>
        current?.session.id === result.session.id ? { ...current, session: result.session } : current
      );
      await refreshSessions(result.session.id, actionVersionId);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleAnswer(event: FormEvent) {
    event.preventDefault();
    if (!selected || clarificationAnswer.trim().length < 2) return;
    const actionSessionId = selected.id;
    const actionVersionId = selectedVersionIdRef.current;
    invalidateWorkspaceReads();
    setIsBusy(true);
    setError(null);
    try {
      const nextWorkspace = await sendMessage(actionSessionId, clarificationAnswer);
      if (!isCurrentActionResponse(actionSessionId, nextWorkspace.session.id, actionVersionId)) {
        return;
      }
      applyWorkspace(nextWorkspace, actionVersionId);
      setClarificationAnswer("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsBusy(false);
    }
  }

  async function persistDraft(nextDraft: Phase2Draft): Promise<boolean> {
    if (!selected) return false;
    const actionSessionId = selected.id;
    const actionVersionId = selectedVersionIdRef.current;
    const draftRequestId = ++draftRequestIdRef.current;
    invalidateWorkspaceReads();
    setIsDraftBusy(true);
    setError(null);
    try {
      const nextWorkspace = await updateDraft(actionSessionId, nextDraft);
      if (
        draftRequestId !== draftRequestIdRef.current ||
        !isCurrentActionResponse(actionSessionId, nextWorkspace.session.id, actionVersionId)
      ) {
        return false;
      }
      applyWorkspace(nextWorkspace, actionVersionId);
      return true;
    } catch (err) {
      if (draftRequestId === draftRequestIdRef.current) {
        setError(errorMessage(err));
      }
      return false;
    } finally {
      if (draftRequestId === draftRequestIdRef.current) {
        setIsDraftBusy(false);
      }
    }
  }

  async function handleCandidateAction(candidate: DecisionCandidate, action: CandidateOverrideAction) {
    if (!workspace) return;
    const nextDraft = draftForEdit(workspace, selectedVersionIdRef.current);
    nextDraft.candidate_overrides = {
      ...nextDraft.candidate_overrides,
      [candidate.slug]: {
        action,
        reason: `User set ${candidate.name} to ${action.replace("_", " ")} in the workbench.`
      }
    };
    await persistDraft(nextDraft);
  }

  async function handleConstraintAction(constraint: string, mode: "include" | "exclude") {
    if (!workspace) return;
    const nextDraft = draftForEdit(workspace, selectedVersionIdRef.current);
    const targetKey = mode === "include" ? "must_include_constraints" : "must_exclude_constraints";
    const oppositeKey = mode === "include" ? "must_exclude_constraints" : "must_include_constraints";
    nextDraft[targetKey] = {
      ...nextDraft[targetKey],
      [constraint]: {
        enabled: true,
        reason: `User explicitly set ${constraint} as a structured ${mode} constraint.`
      }
    };
    const oppositeConstraints = { ...nextDraft[oppositeKey] };
    delete oppositeConstraints[constraint];
    nextDraft[oppositeKey] = oppositeConstraints;
    await persistDraft(nextDraft);
  }

  async function handleWeightApply(criterion: DecisionCriterion) {
    if (!workspace) return;
    const nextDraft = draftForEdit(workspace, selectedVersionIdRef.current);
    const weight = clampWeight(weightDrafts[criterion.name] ?? String(criterion.weight), criterion.weight);
    nextDraft.weight_overrides = {
      ...nextDraft.weight_overrides,
      [criterion.name]: {
        weight,
        reason: `User adjusted ${criterion.name} weight in the workbench.`
      }
    };
    if (await persistDraft(nextDraft)) {
      dirtyWeightNamesRef.current.delete(criterion.name);
    }
  }

  async function handleAddCustomCandidate(event: FormEvent) {
    event.preventDefault();
    if (!workspace) return;
    const slug = customCandidate.slug.trim();
    const repoFullName = customCandidate.repo_full_name.trim();
    if (
      customCandidate.name.trim().length < 2 ||
      slug.length < 2 ||
      repoFullName.length < 3 ||
      !repoFullName.includes("/") ||
      customCandidate.reason.trim().length < 3
    ) {
      return;
    }
    const nextDraft = draftForEdit(workspace, selectedVersionIdRef.current);
    nextDraft.custom_candidates = {
      ...nextDraft.custom_candidates,
      [slug]: {
        name: customCandidate.name.trim(),
        slug,
        repo_full_name: repoFullName,
        reason: customCandidate.reason.trim()
      }
    };
    await persistDraft(nextDraft);
    setCustomCandidate(emptyCustomCandidate);
  }

  async function handleCreateVersion() {
    if (!selected || !workspace || !hasDraftChanges(workspace.draft)) return;
    const actionSessionId = selected.id;
    const actionVersionId = selectedVersionIdRef.current;
    invalidateWorkspaceReads();
    setIsVersionBusy(true);
    setError(null);
    try {
      const result = await createDecisionVersion(actionSessionId);
      if (!isCurrentActionResponse(actionSessionId, result.session.id, actionVersionId)) {
        return;
      }
      selectSession(result.session);
      setWorkspace((current) =>
        current?.session.id === result.session.id ? { ...current, session: result.session } : current
      );
      setSelectedVersionId(null);
      await refreshSessions(result.session.id, null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIsVersionBusy(false);
    }
  }

  const activeVersion = workspace
    ? workspace.versions.find((version) => version.id === selectedVersionId) ?? latestVersion(workspace)
    : null;
  const recommendation = workspace ? activeRecommendation(workspace) : null;
  const recommendationSummary = recommendation?.summary ?? selected?.current_summary ?? null;
  const canRun =
    !!selected &&
    workspace?.session.id === selected.id &&
    workspace.session.workflow_stage === "ready_for_research" &&
    !nonRunnableRunStatuses.has(workspace.session.status);
  const draftChanged = !!workspace && hasDraftChanges(workspace.draft);
  const constraints = useMemo(() => (workspace ? knownConstraints(workspace) : []), [workspace]);
  const selectedScoreCell = workspace?.score_cells.find((scoreCell) => scoreCell.id === selectedScoreCellId) ?? null;
  const selectedEvidence = workspace?.evidence_items.find((item) => item.id === selectedEvidenceId) ?? null;
  const selectedScoreCellEvidence =
    workspace && selectedScoreCell ? evidenceForScoreCell(workspace, selectedScoreCell) : [];
  const activeVersionId = activeVersion?.id ?? null;
  const activeRisks = useMemo(() => (workspace ? activeRiskSignals(workspace) : []), [workspace]);
  const riskCounts = useMemo(() => riskSummaryCounts(activeRisks), [activeRisks]);
  const sourceTypes = useMemo(() => (workspace ? sourceTypesForWorkspace(workspace) : []), [workspace]);
  const candidateNameById = useMemo(
    () => new Map(workspace?.candidates.map((candidate) => [candidate.id, candidate.name]) ?? []),
    [workspace]
  );
  const selectedRisk = activeRisks.find((risk) => risk.id === selectedRiskId) ?? null;
  const selectedRiskClaims =
    workspace && selectedRisk ? claimsSupportingRisk(workspace, selectedRisk) : [];
  const selectedRiskTasks =
    workspace && selectedRisk ? verificationTasksForRisk(workspace, selectedRisk) : [];
  const visibleClaims = useMemo(
    () => (workspace ? activeClaims(workspace).filter((claim) => sourceMatches(claim, sourceTypeFilter)) : []),
    [workspace, sourceTypeFilter]
  );
  const visibleEvidenceItems = useMemo(
    () =>
      workspace
        ? workspace.evidence_items.filter(
            (item) => versionMatches(item, activeVersionId) && sourceMatches(item, sourceTypeFilter)
          )
        : [],
    [workspace, activeVersionId, sourceTypeFilter]
  );
  const visibleVerificationTasks = useMemo(
    () =>
      workspace
        ? activeVerificationTasks(workspace).filter(
            (task) => !sourceTypeFilter || task.stronger_source_type === sourceTypeFilter
          )
        : [],
    [workspace, sourceTypeFilter]
  );
  const toolCallGroups = useMemo(
    () => (workspace ? toolCallsGroupedByStatusAndSource(workspace, sourceTypeFilter) : []),
    [workspace, sourceTypeFilter]
  );

  if (isAdminRoute) {
    return (
      <main className="app-shell admin-shell">
        <section className="admin-panel" aria-label="Demo admin">
          <p className="eyebrow">PuppyRun Phase 5</p>
          <h1>Public demo controls</h1>
          <label htmlFor="admin-token">Admin token</label>
          <input
            id="admin-token"
            onChange={(event) => setAdminToken(event.target.value)}
            type="password"
            value={adminToken}
          />
          <div className="admin-actions">
            <button
              disabled={isAdminBusy || adminToken.trim().length === 0}
              onClick={loadAdminStatus}
              type="button"
            >
              Load admin status
            </button>
            <button
              disabled={isAdminBusy || !adminStatus}
              onClick={() => handleAdminToggle(false)}
              type="button"
            >
              Disable live demo
            </button>
            <button
              disabled={isAdminBusy || !adminStatus}
              onClick={() => handleAdminToggle(true)}
              type="button"
            >
              Enable live demo
            </button>
          </div>
          {adminError && <p className="error">{adminError}</p>}
          {adminStatus && (
            <section className="admin-status" aria-label="Demo safety status">
              <h2>{adminStatus.live_demo_enabled ? "Live demo enabled" : "Live demo disabled"}</h2>
              <dl>
                <div>
                  <dt>Global live runs</dt>
                  <dd>
                    {adminStatus.global_live_runs_used} / {adminStatus.global_live_run_daily_limit}
                  </dd>
                </div>
                <div>
                  <dt>Your live runs</dt>
                  <dd>
                    {adminStatus.caller_live_runs_used} / {adminStatus.live_run_daily_limit_per_ip}
                  </dd>
                </div>
                <div>
                  <dt>Your sessions</dt>
                  <dd>
                    {adminStatus.caller_session_creates_used} /{" "}
                    {adminStatus.session_create_daily_limit_per_ip}
                  </dd>
                </div>
                <div>
                  <dt>Read limit</dt>
                  <dd>{adminStatus.read_rate_limit_per_minute_per_ip} / minute</dd>
                </div>
                <div>
                  <dt>Reset</dt>
                  <dd>{new Date(adminStatus.reset_at).toLocaleString()}</dd>
                </div>
              </dl>
            </section>
          )}
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="top-bar">
        <div>
          <p className="eyebrow">PuppyRun Phase 2</p>
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
                onClick={() => loadWorkspace(session).catch((err: unknown) => setError(errorMessage(err)))}
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
            <div>
              <span>{workspace?.session.workflow_stage ?? "no_session"}</span>
              {activeVersion && <strong>Version {activeVersion.version_number}</strong>}
            </div>
            <div className="stage-actions">
              <button disabled={!canRun || isBusy} onClick={handleRun} type="button">
                Run Phase 1 Agent
              </button>
              <button
                disabled={!draftChanged || isDraftBusy || isVersionBusy || !selected}
                onClick={handleCreateVersion}
                type="button"
              >
                Run targeted re-research
              </button>
            </div>
          </div>

          <nav className="version-rail" aria-label="Decision versions">
            {workspace?.versions.length ? (
              workspace.versions.map((version) => (
                <button
                  aria-label={`Version ${version.version_number} ${version.status}`}
                  className={version.id === selectedVersionId ? "version-pill active" : "version-pill"}
                  key={version.id}
                  onClick={() =>
                    selected && loadWorkspace(selected, version.id).catch((err) => setError(errorMessage(err)))
                  }
                  type="button"
                >
                  <span>v{version.version_number}</span>
                  <strong>{version.status}</strong>
                </button>
              ))
            ) : (
              <p>No versions yet.</p>
            )}
          </nav>

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

          <section className="workbench-panel" aria-label="Workbench controls">
            <h2>Candidate controls</h2>
            {workspace?.candidates.map((candidate) => (
              <article className="candidate-control" key={candidate.id}>
                <div>
                  <strong>{candidate.name}</strong>
                  <span>{candidate.selection_state}</span>
                </div>
                <p>{candidate.include_reason}</p>
                <div className="control-row">
                  {candidateActions.map((item) => (
                    <button
                      aria-label={`${item.label} ${candidate.name}`}
                      disabled={isDraftBusy}
                      key={item.action}
                      onClick={() => handleCandidateAction(candidate, item.action)}
                      type="button"
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </article>
            ))}

            <h2>Explicit constraints</h2>
            {constraints.length > 0 ? (
              constraints.map((constraint) => (
                <article className="constraint-row" key={constraint}>
                  <strong>{constraint}</strong>
                  <div className="control-row">
                    <button
                      aria-label={`Must include ${constraint}`}
                      disabled={isDraftBusy}
                      onClick={() => handleConstraintAction(constraint, "include")}
                      type="button"
                    >
                      Must include
                    </button>
                    <button
                      aria-label={`Must exclude ${constraint}`}
                      disabled={isDraftBusy}
                      onClick={() => handleConstraintAction(constraint, "exclude")}
                      type="button"
                    >
                      Must exclude
                    </button>
                  </div>
                </article>
              ))
            ) : (
              <p>No structured constraints yet.</p>
            )}

            <h2>Weight editor</h2>
            {workspace?.criteria.map((criterion) => (
              <article className="weight-row" key={criterion.id}>
                <label htmlFor={`weight-${criterion.id}`}>Weight for {criterion.name}</label>
                <div className="weight-controls">
                  <input
                    id={`weight-${criterion.id}`}
                    max={100}
                    min={0}
                    onChange={(event) =>
                      setWeightDrafts((current) => {
                        dirtyWeightNamesRef.current.add(criterion.name);
                        return { ...current, [criterion.name]: event.target.value };
                      })
                    }
                    type="number"
                    value={weightDrafts[criterion.name] ?? String(criterion.weight)}
                  />
                  <button
                    aria-label={`Apply ${criterion.name} weight`}
                    disabled={isDraftBusy}
                    onClick={() => handleWeightApply(criterion)}
                    type="button"
                  >
                    Apply
                  </button>
                </div>
                <p>{criterion.rationale}</p>
              </article>
            ))}

            <form className="custom-candidate-form" onSubmit={handleAddCustomCandidate}>
              <h2>Custom candidate</h2>
              <label htmlFor="custom-candidate-name">Custom candidate name</label>
              <input
                id="custom-candidate-name"
                onChange={(event) =>
                  setCustomCandidate((current) => ({ ...current, name: event.target.value }))
                }
                value={customCandidate.name}
              />
              <label htmlFor="custom-candidate-slug">Custom candidate slug</label>
              <input
                id="custom-candidate-slug"
                onChange={(event) =>
                  setCustomCandidate((current) => ({ ...current, slug: event.target.value }))
                }
                value={customCandidate.slug}
              />
              <label htmlFor="custom-candidate-repo">Custom candidate repository</label>
              <input
                id="custom-candidate-repo"
                onChange={(event) =>
                  setCustomCandidate((current) => ({ ...current, repo_full_name: event.target.value }))
                }
                placeholder="owner/repo"
                value={customCandidate.repo_full_name}
              />
              <label htmlFor="custom-candidate-reason">Custom candidate reason</label>
              <textarea
                id="custom-candidate-reason"
                onChange={(event) =>
                  setCustomCandidate((current) => ({ ...current, reason: event.target.value }))
                }
                value={customCandidate.reason}
              />
              <button disabled={isDraftBusy} type="submit">
                Add custom candidate
              </button>
            </form>
          </section>

          <section className="gap-panel" aria-label="Gap analysis">
            <h2>Gap analysis</h2>
            <p>{workspace ? gapSummary(workspace.gap_analysis) : "No workspace loaded."}</p>
            {workspace && (
              <dl>
                <div>
                  <dt>Changed candidates</dt>
                  <dd>{formatList(workspace.gap_analysis.changed_candidates)}</dd>
                </div>
                <div>
                  <dt>Changed constraints</dt>
                  <dd>{formatList(workspace.gap_analysis.changed_constraints)}</dd>
                </div>
                <div>
                  <dt>Changed weights</dt>
                  <dd>{formatList(workspace.gap_analysis.changed_weights)}</dd>
                </div>
                <div>
                  <dt>Research tasks</dt>
                  <dd>{workspace.gap_analysis.research_tasks.length}</dd>
                </div>
              </dl>
            )}
          </section>
        </section>

        <aside className="evidence-column" aria-label="Evidence and trace">
          <section className="source-filter-section" aria-label="Source filters">
            <div className="panel-header">
              <h2>Source filters</h2>
              <span>{sourceTypeFilter ?? "all"}</span>
            </div>
            <div className="source-filter-row">
              <button
                aria-label="Filter source all"
                className={sourceTypeFilter === null ? "source-filter active" : "source-filter"}
                onClick={() => setSourceTypeFilter(null)}
                type="button"
              >
                All
              </button>
              {sourceTypes.map((sourceType) => (
                <button
                  aria-label={`Filter source ${sourceType}`}
                  className={sourceTypeFilter === sourceType ? "source-filter active" : "source-filter"}
                  key={sourceType}
                  onClick={() => setSourceTypeFilter(sourceType)}
                  type="button"
                >
                  {sourceType}
                </button>
              ))}
            </div>
          </section>

          <section className="risk-panel" aria-label="Risk panel">
            <div className="panel-header">
              <h2>Risk panel</h2>
              <span>{riskCounts.total} risks</span>
            </div>
            <dl className="risk-counts">
              <div>
                <dt>Confirmed</dt>
                <dd>{riskCounts.confirmed}</dd>
              </div>
              <div>
                <dt>Contradicted</dt>
                <dd>{riskCounts.contradicted}</dd>
              </div>
              <div>
                <dt>Unresolved</dt>
                <dd>{riskCounts.unresolved}</dd>
              </div>
              <div>
                <dt>Unverified</dt>
                <dd>{riskCounts.unverified}</dd>
              </div>
            </dl>
            {activeRisks.length > 0 ? (
              <div className="risk-list">
                {activeRisks.map((risk) => (
                  <button
                    aria-label={`Open risk ${risk.title}`}
                    className={risk.id === selectedRiskId ? "risk-row selected" : "risk-row"}
                    key={risk.id}
                    onClick={() => setSelectedRiskId(risk.id)}
                    type="button"
                  >
                    <span className={`status-pill status-${risk.status}`}>{risk.status}</span>
                    <strong>{risk.title}</strong>
                    <p>{risk.summary}</p>
                    <small>
                      {candidateNameById.get(risk.candidate_id) ?? "Unknown candidate"} / {risk.severity} / impact{" "}
                      {scoreImpactLabel(risk.score_impact)}
                    </small>
                  </button>
                ))}
              </div>
            ) : (
              <p>No Phase 3 risks yet.</p>
            )}
          </section>

          <section className="risk-detail" aria-label="Risk detail">
            <h2>Risk detail</h2>
            {selectedRisk && workspace ? (
              <div>
                <div className="risk-detail-summary">
                  <span className={`status-pill status-${selectedRisk.status}`}>
                    {selectedRisk.status}
                  </span>
                  <strong>{selectedRisk.title}</strong>
                  <p>{selectedRisk.summary}</p>
                </div>
                <h3>Supporting claims</h3>
                {selectedRiskClaims.length > 0 ? (
                  selectedRiskClaims.map((claim) => {
                    const claimEvidence = evidenceForClaim(workspace, claim);
                    return (
                      <article className="claim-row" key={claim.id}>
                        <strong>{claim.title}</strong>
                        <span>
                          {claim.source_type} / {claim.credibility} / {claim.confidence}%
                        </span>
                        <p>{claim.summary}</p>
                        <blockquote>{claim.citation_text}</blockquote>
                        {claimEvidence ? (
                          <a href={claimEvidence.source_url} target="_blank" rel="noreferrer">
                            {claimEvidence.title}
                          </a>
                        ) : (
                          <a href={claim.source_url} target="_blank" rel="noreferrer">
                            {claim.source_url}
                          </a>
                        )}
                      </article>
                    );
                  })
                ) : (
                  <p>No supporting claims recorded.</p>
                )}
                <h3>Verification tasks</h3>
                {selectedRiskTasks.length > 0 ? (
                  selectedRiskTasks.map((task) => (
                    <article className="verification-row" key={task.id}>
                      <strong>{task.verdict ?? task.status}</strong>
                      <p>{task.verification_question}</p>
                      <span>{task.rationale ?? `Needs ${sourceTypeLabel(task.stronger_source_type)}`}</span>
                    </article>
                  ))
                ) : (
                  <p>No verification tasks recorded.</p>
                )}
              </div>
            ) : (
              <p>Select a risk to inspect claims, evidence, and verification tasks.</p>
            )}
          </section>

          <section className="claim-section" aria-label="Claims">
            <h2>Claims</h2>
            {visibleClaims.length > 0 ? (
              visibleClaims.map((claim) => (
                <article className="claim-row" key={claim.id}>
                  <strong>{claim.title}</strong>
                  <span>
                    {claim.source_type} / {claim.credibility} / {claim.confidence}%
                  </span>
                  <p>{claim.summary}</p>
                </article>
              ))
            ) : (
              <p>No claims for this source filter.</p>
            )}
          </section>

          <section className="verification-section" aria-label="Verification tasks">
            <h2>Verification tasks</h2>
            {visibleVerificationTasks.length > 0 ? (
              visibleVerificationTasks.map((task) => (
                <article className="verification-row" key={task.id}>
                  <strong>{task.verdict ?? task.status}</strong>
                  <p>{task.verification_question}</p>
                  <span>{task.rationale ?? `Target: ${sourceTypeLabel(task.stronger_source_type)}`}</span>
                </article>
              ))
            ) : (
              <p>No verification tasks for this source filter.</p>
            )}
          </section>

          <section className="tool-call-section" aria-label="Tool calls">
            <h2>Tool calls</h2>
            {toolCallGroups.length > 0 ? (
              toolCallGroups.map((group) => (
                <div className="tool-call-group" key={group.key}>
                  <div className="tool-call-group-header">
                    <strong>{group.source_type}</strong>
                    <span>{group.status.toUpperCase()}</span>
                    <small>{group.items.length}</small>
                  </div>
                  {group.items.map((toolCall) => (
                    <article className="tool-call-row" key={toolCall.id}>
                      <strong>{toolCall.tool_name}</strong>
                      <span>{toolCall.status}</span>
                      <p>{toolCall.response_summary ?? toolCall.error ?? toolCall.request_summary}</p>
                    </article>
                  ))}
                </div>
              ))
            ) : (
              <p>No tool calls for this source filter.</p>
            )}
          </section>

          <section className="matrix-section">
            <h2>Evidence matrix</h2>
            {workspace?.candidates.length && workspace.criteria.length ? (
              <div className="matrix-scroll">
                <table className="evidence-matrix">
                  <thead>
                    <tr>
                      <th>Candidate</th>
                      {workspace.criteria.map((criterion) => (
                        <th key={criterion.id}>{criterion.name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {workspace.candidates.map((candidate) => (
                      <tr key={candidate.id}>
                        <th>{candidate.name}</th>
                        {workspace.criteria.map((criterion) => {
                          const scoreCell = scoreCellFor(workspace, candidate.id, criterion.id);
                          return (
                            <td key={criterion.id}>
                              {scoreCell ? (
                                <button
                                  aria-label={`Open evidence for ${candidate.name} ${criterion.name}`}
                                  className="score-cell-button"
                                  onClick={() => {
                                    setSelectedScoreCellId(scoreCell.id);
                                    setSelectedEvidenceId(null);
                                  }}
                                  type="button"
                                >
                                  <strong>{scoreCell.score}</strong>
                                  <span>{scoreCell.status}</span>
                                </button>
                              ) : (
                                <span className="empty-cell">No score</span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p>No score matrix yet.</p>
            )}
          </section>

          <section className="evidence-drawer" aria-label="Evidence drawer">
            <h2>Evidence drawer</h2>
            {selectedScoreCell ? (
              <div>
                <p>{selectedScoreCell.explanation}</p>
                {selectedScoreCellEvidence.map((item) => (
                  <article className="evidence-row" key={item.id}>
                    <a href={item.source_url} target="_blank" rel="noreferrer">
                      {item.title}
                    </a>
                    <p>{item.summary}</p>
                  </article>
                ))}
              </div>
            ) : selectedEvidence ? (
              <article className="evidence-row">
                <a href={selectedEvidence.source_url} target="_blank" rel="noreferrer">
                  {selectedEvidence.title}
                </a>
                <p>{selectedEvidence.summary}</p>
              </article>
            ) : (
              <p>Select a score cell or evidence item.</p>
            )}
          </section>

          <section className="adr-section" aria-label="ADR">
            <h2>ADR</h2>
            <pre>{activeVersion?.adr ?? "No ADR yet."}</pre>
          </section>

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

          <section className="source-evidence-section" aria-label="Evidence list">
            <h2>Evidence</h2>
            {visibleEvidenceItems.length > 0 ? (
              visibleEvidenceItems.map((item) => (
                <article className="evidence-row" key={item.id}>
                  <button
                    className="text-button"
                    onClick={() => {
                      setSelectedEvidenceId(item.id);
                      setSelectedScoreCellId(null);
                    }}
                    type="button"
                  >
                    {item.title}
                  </button>
                  <span className="source-tag">{item.source_type}</span>
                  <p>{item.summary}</p>
                </article>
              ))
            ) : (
              <p>No evidence for this source filter.</p>
            )}
          </section>

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
