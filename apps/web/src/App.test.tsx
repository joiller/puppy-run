import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import {
  createDecisionVersion,
  createSession,
  getWorkspace,
  listSessions,
  sendMessage,
  startRun,
  updateDraft
} from "./api";
import type {
  DecisionSession,
  DecisionVersion,
  GapAnalysis,
  Phase2Draft,
  StartAgentRunResponse,
  Workspace
} from "./types";
import {
  activeRecommendation,
  activeRiskSignals,
  claimsSupportingRisk,
  evidenceForScoreCell,
  gapSummary,
  latestVersion,
  riskSummaryCounts,
  scoreCellFor,
  toolCallsGroupedByStatusAndSource,
  verificationTasksForRisk
} from "./workbench";

vi.mock("./api", () => ({
  ApiError: class ApiError extends Error {},
  createDecisionVersion: vi.fn(),
  createSession: vi.fn(),
  getWorkspace: vi.fn(),
  listSessions: vi.fn(),
  sendMessage: vi.fn(),
  startRun: vi.fn(),
  updateDraft: vi.fn()
}));

const createDecisionVersionMock = vi.mocked(createDecisionVersion);
const createSessionMock = vi.mocked(createSession);
const getWorkspaceMock = vi.mocked(getWorkspace);
const listSessionsMock = vi.mocked(listSessions);
const sendMessageMock = vi.mocked(sendMessage);
const startRunMock = vi.mocked(startRun);
const updateDraftMock = vi.mocked(updateDraft);
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
    decision_context: {
      domain: "agent_framework_selection",
      constraints: ["checkpointing", "python"]
    },
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

function makeDraft(sourceVersionId: string | null = null, overrides: Partial<Phase2Draft> = {}): Phase2Draft {
  return {
    source_version_id: sourceVersionId,
    candidate_overrides: {},
    custom_candidates: {},
    must_include_constraints: {},
    must_exclude_constraints: {},
    weight_overrides: {},
    ...overrides
  };
}

function makeGap(overrides: Partial<GapAnalysis> = {}): GapAnalysis {
  return {
    requires_research: false,
    requires_github_fetch: false,
    score_only: false,
    changed_candidates: [],
    changed_constraints: [],
    changed_weights: [],
    research_tasks: [],
    reuse_tasks: [],
    items: [],
    ...overrides
  };
}

function gapFromDraft(draft: Phase2Draft): GapAnalysis {
  const changedCandidates = Array.from(
    new Set([...Object.keys(draft.candidate_overrides), ...Object.keys(draft.custom_candidates)])
  );
  const changedConstraints = Array.from(
    new Set([
      ...Object.keys(draft.must_include_constraints),
      ...Object.keys(draft.must_exclude_constraints)
    ])
  );
  const changedWeights = Object.keys(draft.weight_overrides);
  return makeGap({
    requires_research: changedCandidates.length > 0 || changedConstraints.length > 0,
    score_only: changedCandidates.length === 0 && changedConstraints.length === 0 && changedWeights.length > 0,
    changed_candidates: changedCandidates,
    changed_constraints: changedConstraints,
    changed_weights: changedWeights,
    research_tasks: changedCandidates.map((candidate) => ({ candidate }))
  });
}

function makeVersion(versionNumber: number, overrides: Partial<DecisionVersion> = {}): DecisionVersion {
  return {
    id: `version-${versionNumber}`,
    session_id: "session-1",
    version_number: versionNumber,
    label: `Version ${versionNumber}`,
    status: "completed",
    source_version_id: versionNumber === 1 ? null : `version-${versionNumber - 1}`,
    change_summary: {},
    gap_analysis: {},
    adr: `ADR v${versionNumber}: choose ${versionNumber === 1 ? "LangGraph" : "OpenAI Agents SDK"}.`,
    created_at: `2026-05-2${versionNumber}T00:00:00Z`,
    completed_at: `2026-05-2${versionNumber}T00:00:00Z`,
    ...overrides
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

function getTargetedRunButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Run targeted re-research" }) as HTMLButtonElement;
}

function getSendAnswerButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Send answer" }) as HTMLButtonElement;
}

function makeWorkspace(
  session: DecisionSession,
  extraMessages: Array<{ role: string; content: string }> = [],
  overrides: Partial<Workspace> = {}
): Workspace {
  const draft = makeDraft(null);
  const baseWorkspace: Workspace = {
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
    versions: [],
    active_version: null,
    draft,
    gap_analysis: makeGap(),
    candidates: [],
    criteria: [],
    evidence_items: [],
    tool_calls: [],
    claims: [],
    risk_signals: [],
    verification_tasks: [],
    score_cells: [],
    recommendations: [],
    events: []
  };
  return { ...baseWorkspace, ...overrides };
}

function makeCompletedWorkspace(session: DecisionSession, overrides: Partial<Workspace> = {}): Workspace {
  const version = overrides.active_version ?? makeVersion(1, { session_id: session.id });
  const draft = overrides.draft ?? makeDraft(version.id);
  const candidateId = version.version_number === 1 ? "candidate-1" : "candidate-2";
  const candidateName = version.version_number === 1 ? "LangGraph" : "OpenAI Agents SDK";
  const criterionId = "criterion-1";
  const evidenceId = version.version_number === 1 ? "evidence-1" : "evidence-2";
  return {
    ...makeWorkspace(session),
    versions: overrides.versions ?? [version],
    active_version: version,
    draft,
    gap_analysis: overrides.gap_analysis ?? makeGap(),
    candidates: [
      {
        id: candidateId,
        session_id: session.id,
        decision_version_id: version.id,
        name: candidateName,
        slug: version.version_number === 1 ? "langgraph" : "openai-agents-sdk",
        repo_full_name:
          version.version_number === 1 ? "langchain-ai/langgraph" : "openai/openai-agents-python",
        include_reason: "Included for checkpointed stateful workflows.",
        health_summary: `${candidateName}: 50000 stars.`,
        health_metrics: { stars: 50000 },
        score: version.version_number === 1 ? 92 : 88,
        selection_state: "included",
        is_locked: false,
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    criteria: [
      {
        id: criterionId,
        session_id: session.id,
        decision_version_id: version.id,
        name: "Runtime control and state",
        weight: 30,
        rationale: "State handling is central for long-running Agent workflows.",
        evidence_needed: "Checkpoint and state support.",
        is_locked: false,
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    evidence_items: [
      {
        id: evidenceId,
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        criterion_id: null,
        source_type: "github_repo",
        source_url:
          version.version_number === 1
            ? "https://github.com/langchain-ai/langgraph"
            : "https://github.com/openai/openai-agents-python",
        title: `GitHub repository health for ${candidateName}`,
        summary: `${candidateName}: 50000 stars.`,
        credibility: "medium",
        payload: { stars: 50000 },
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    score_cells: [
      {
        id: `score-cell-${version.version_number}`,
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        criterion_id: criterionId,
        score: version.version_number === 1 ? 92 : 88,
        status: "supported",
        explanation: `${candidateName} has strong runtime evidence.`,
        evidence_item_ids: [evidenceId],
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    recommendations: [
      {
        id: `recommendation-${version.version_number}`,
        session_id: session.id,
        decision_version_id: version.id,
        recommended_candidate_id: candidateId,
        summary:
          session.current_summary ??
          `Recommended: ${candidateName}. It scored ${version.version_number === 1 ? 92 : 88}/100.`,
        rationale: { recommended_slug: version.version_number === 1 ? "langgraph" : "openai-agents-sdk" },
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    events: [
      {
        id: "event-1",
        run_id: "run-1",
        event_type: "recommendation_generated",
        message: session.current_summary ?? `Recommended: ${candidateName}.`,
        payload: {},
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    ...overrides
  };
}

function makePhase3Workspace(session: DecisionSession): Workspace {
  const version = makeVersion(1, { session_id: session.id });
  const workspace = makeCompletedWorkspace(session, {
    versions: [version],
    active_version: version,
    draft: makeDraft(version.id)
  });
  const candidateId = workspace.candidates[0].id;
  return {
    ...workspace,
    evidence_items: [
      ...workspace.evidence_items,
      {
        id: "evidence-github-risk",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        criterion_id: null,
        source_type: "github_issue",
        source_url: "https://github.com/langchain-ai/langgraph/issues/321",
        title: "GitHub issue: stale checkpoint bug",
        summary: "Maintainers confirmed checkpoint state can be stale after interrupted runs.",
        credibility: "high",
        payload: {},
        created_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "evidence-reddit-risk",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        criterion_id: null,
        source_type: "reddit",
        source_url: "https://reddit.com/r/agents/comments/example",
        title: "Community thread: stalled LangGraph releases",
        summary: "Community users report stalled release cadence for long-running Agent workflows.",
        credibility: "low",
        payload: {},
        created_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "evidence-docs-risk",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        criterion_id: null,
        source_type: "official_docs",
        source_url: "https://langchain-ai.github.io/langgraph/concepts/persistence/",
        title: "Official docs: checkpoint durability",
        summary: "Official docs describe supported persistence semantics for checkpoints.",
        credibility: "high",
        payload: {},
        created_at: "2026-05-27T00:00:00Z"
      }
    ],
    tool_calls: [
      {
        id: "tool-call-1",
        session_id: session.id,
        decision_version_id: version.id,
        tool_name: "phase3_candidate_sources",
        status: "completed",
        idempotency_key: "phase3:sources:langgraph",
        source_type: "github_issue",
        source_url: "https://github.com/langchain-ai/langgraph/issues",
        request_summary: "Collect GitHub issue source snippets for LangGraph.",
        response_summary: "Collected 1 GitHub issue source.",
        payload: {},
        error: null,
        started_at: "2026-05-27T00:00:00Z",
        completed_at: "2026-05-27T00:00:01Z",
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:01Z"
      },
      {
        id: "tool-call-2",
        session_id: session.id,
        decision_version_id: version.id,
        tool_name: "phase3_reddit_search",
        status: "skipped",
        idempotency_key: "phase3:reddit:langgraph",
        source_type: "reddit",
        source_url: null,
        request_summary: "Search Reddit for LangGraph risk reports.",
        response_summary: "Skipped because Reddit search is disabled.",
        payload: {},
        error: null,
        started_at: null,
        completed_at: "2026-05-27T00:00:02Z",
        created_at: "2026-05-27T00:00:02Z",
        updated_at: "2026-05-27T00:00:02Z"
      }
    ],
    claims: [
      {
        id: "claim-confirmed",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        criterion_id: null,
        source_evidence_item_id: "evidence-github-risk",
        source_type: "github_issue",
        source_url: "https://github.com/langchain-ai/langgraph/issues/321",
        title: "Checkpoint bug is confirmed",
        summary: "Checkpoint state can be stale after interrupted runs.",
        citation_text: "Maintainers confirmed stale checkpoint state after interrupts.",
        credibility: "high",
        confidence: 88,
        content_hash: "claim-confirmed-hash",
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "claim-contradicted",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        criterion_id: null,
        source_evidence_item_id: "evidence-docs-risk",
        source_type: "official_docs",
        source_url: "https://langchain-ai.github.io/langgraph/concepts/persistence/",
        title: "Durability concern is contradicted",
        summary: "Official docs document supported checkpoint durability.",
        citation_text: "Persistence docs describe durable checkpoint writes.",
        credibility: "high",
        confidence: 81,
        content_hash: "claim-contradicted-hash",
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "claim-unresolved",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        criterion_id: null,
        source_evidence_item_id: "evidence-reddit-risk",
        source_type: "reddit",
        source_url: "https://reddit.com/r/agents/comments/example",
        title: "Release cadence is unresolved",
        summary: "Community users report stalled releases for long-running workflows.",
        citation_text: "Multiple users mention delayed releases.",
        credibility: "low",
        confidence: 54,
        content_hash: "claim-unresolved-hash",
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "claim-unverified",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        criterion_id: null,
        source_evidence_item_id: "evidence-reddit-risk",
        source_type: "reddit",
        source_url: "https://reddit.com/r/agents/comments/example",
        title: "Upgrade churn is unverified",
        summary: "A community report claims migration churn without stronger support.",
        citation_text: "One user claims migration churn.",
        credibility: "low",
        confidence: 43,
        content_hash: "claim-unverified-hash",
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      }
    ],
    risk_signals: [
      {
        id: "risk-confirmed",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        risk_key: "checkpoint_staleness",
        title: "Checkpoint staleness",
        summary: "Interrupted runs can leave stale checkpoint state.",
        severity: "high",
        status: "confirmed",
        credibility: "high",
        score_impact: -8,
        supporting_claim_ids: ["claim-confirmed"],
        verification_task_ids: ["task-confirmed"],
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "risk-contradicted",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        risk_key: "durability_gap",
        title: "Durability gap",
        summary: "A suspected durability gap is contradicted by official docs.",
        severity: "medium",
        status: "contradicted",
        credibility: "high",
        score_impact: 0,
        supporting_claim_ids: ["claim-contradicted"],
        verification_task_ids: ["task-contradicted"],
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "risk-unresolved",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        risk_key: "release_cadence",
        title: "Release cadence",
        summary: "Release cadence concern still needs stronger evidence.",
        severity: "medium",
        status: "unresolved",
        credibility: "low",
        score_impact: 0,
        supporting_claim_ids: ["claim-unresolved"],
        verification_task_ids: ["task-unresolved"],
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "risk-unverified",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        risk_key: "upgrade_churn",
        title: "Upgrade churn",
        summary: "Upgrade churn is visible but unverified.",
        severity: "low",
        status: "unverified",
        credibility: "low",
        score_impact: 0,
        supporting_claim_ids: ["claim-unverified"],
        verification_task_ids: ["task-unverified"],
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      }
    ],
    verification_tasks: [
      {
        id: "task-confirmed",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        risk_signal_id: "risk-confirmed",
        status: "completed",
        verification_question: "Does stronger evidence confirm checkpoint staleness?",
        stronger_source_type: "github_issue",
        stronger_source_url: "https://github.com/langchain-ai/langgraph/issues/321",
        verdict: "confirmed",
        rationale: "Maintainer issue confirms the failure mode.",
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "task-contradicted",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        risk_signal_id: "risk-contradicted",
        status: "completed",
        verification_question: "Do official docs contradict the durability gap?",
        stronger_source_type: "official_docs",
        stronger_source_url: "https://langchain-ai.github.io/langgraph/concepts/persistence/",
        verdict: "contradicted",
        rationale: "Official docs describe durable persistence support.",
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "task-unresolved",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        risk_signal_id: "risk-unresolved",
        status: "completed",
        verification_question: "Can release cadence be verified from a stronger source?",
        stronger_source_type: "official_release",
        stronger_source_url: null,
        verdict: "unresolved",
        rationale: "No official release note confirms or contradicts the community report.",
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      },
      {
        id: "task-unverified",
        session_id: session.id,
        decision_version_id: version.id,
        candidate_id: candidateId,
        risk_signal_id: "risk-unverified",
        status: "planned",
        verification_question: "Find stronger evidence for upgrade churn.",
        stronger_source_type: "official_docs",
        stronger_source_url: null,
        verdict: null,
        rationale: null,
        payload: {},
        created_at: "2026-05-27T00:00:00Z",
        updated_at: "2026-05-27T00:00:00Z"
      }
    ]
  };
}

describe("workbench helpers", () => {
  it("selects versions, recommendations, score cells, evidence, and gap summaries", () => {
    const completed = makeSession("completed", "Recommended: LangGraph. It scored 92/100.");
    const workspace = makeCompletedWorkspace(completed);
    const activeVersion = latestVersion(workspace);

    expect(activeVersion?.id).toBe("version-1");
    expect(activeRecommendation(workspace)?.summary).toContain("LangGraph");
    const scoreCell = scoreCellFor(workspace, "candidate-1", "criterion-1");
    expect(scoreCell?.score).toBe(92);
    expect(scoreCell ? evidenceForScoreCell(workspace, scoreCell).map((item) => item.id) : []).toEqual([
      "evidence-1"
    ]);
    expect(gapSummary(makeGap())).toBe("No draft changes yet.");

    const fallbackWorkspace = { ...workspace, active_version: null };
    expect(latestVersion(fallbackWorkspace)?.id).toBe("version-1");
  });

  it("selects Phase 3 risks, claims, tasks, tool groups, and summary counts", () => {
    const workspace = makePhase3Workspace(makeSession("completed", "Recommended: LangGraph."));
    const risks = activeRiskSignals(workspace);
    const confirmedRisk = risks.find((risk) => risk.status === "confirmed");

    expect(workspace.tool_calls.map((toolCall) => toolCall.status)).toEqual(["completed", "skipped"]);
    expect(workspace.claims).toHaveLength(4);
    expect(riskSummaryCounts(risks)).toEqual({
      total: 4,
      confirmed: 1,
      contradicted: 1,
      unresolved: 1,
      unverified: 1
    });
    expect(confirmedRisk ? claimsSupportingRisk(workspace, confirmedRisk).map((claim) => claim.id) : []).toEqual([
      "claim-confirmed"
    ]);
    expect(
      confirmedRisk ? verificationTasksForRisk(workspace, confirmedRisk).map((task) => task.id) : []
    ).toEqual(["task-confirmed"]);
    expect(toolCallsGroupedByStatusAndSource(workspace).map((group) => group.key)).toEqual([
      "completed:github_issue",
      "skipped:reddit"
    ]);
  });
});

describe("api functions", () => {
  it("calls Phase 2 draft, version, and versioned workspace endpoints", async () => {
    const completed = makeSession("completed", "Recommended: LangGraph.");
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(makeCompletedWorkspace(completed)), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    const actualApi = await vi.importActual<typeof import("./api")>("./api");
    const draft = makeDraft("version-1", {
      weight_overrides: {
        "Runtime control and state": { weight: 45, reason: "Runtime recovery matters most." }
      }
    });

    await actualApi.updateDraft("session-1", draft);
    await actualApi.createDecisionVersion("session-1");
    await actualApi.getWorkspace("session-1", "version-2");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://localhost:8000/api/v1/sessions/session-1/draft",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify(draft) })
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://localhost:8000/api/v1/sessions/session-1/versions",
      expect.objectContaining({ method: "POST" })
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://localhost:8000/api/v1/sessions/session-1/workspace?version_id=version-2",
      expect.any(Object)
    );
  });

  it("throws typed API errors for demo safety responses", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          code: "live_run_daily_limit_exceeded",
          message: "The public live demo has reached today's run limit.",
          limit: 20,
          remaining: 0,
          reset_at: "2026-06-21T00:00:00Z"
        }),
        {
          status: 429,
          headers: { "Content-Type": "application/json" }
        }
      )
    );
    vi.stubGlobal("fetch", fetchMock);
    const actualApi = await vi.importActual<typeof import("./api")>("./api");

    await expect(actualApi.startRun("session-1")).rejects.toMatchObject({
      status: 429,
      code: "live_run_daily_limit_exceeded",
      message: "The public live demo has reached today's run limit."
    });
  });
});

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
    createDecisionVersionMock.mockReset();
    createSessionMock.mockReset();
    getWorkspaceMock.mockReset();
    listSessionsMock.mockReset();
    sendMessageMock.mockReset();
    startRunMock.mockReset();
    updateDraftMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows friendly public demo quota errors", async () => {
    const ready = makeSession("created");
    const workspace = makeWorkspace(
      Object.assign({}, ready, { workflow_stage: "ready_for_research" })
    );

    listSessionsMock.mockImplementation(async () => [workspace.session]);
    getWorkspaceMock.mockImplementation(async () => workspace);
    startRunMock.mockRejectedValue({
      status: 429,
      code: "live_run_daily_limit_exceeded",
      message: "The public live demo has reached today's run limit. Please try again after the reset."
    });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(getRunButton().disabled).toBe(false);
    });
    fireEvent.click(getRunButton());

    await waitFor(() => {
      expect(screen.getByText(/public live demo has reached today's run limit/i)).toBeTruthy();
    });
  });

  it("shows clarification, recommendation, evidence, trace, version, matrix, drawer, and ADR for a Phase 1 run", async () => {
    const created = makeSession("created");
    const ready: DecisionSession = { ...created, workflow_stage: "ready_for_research" };
    const completed: DecisionSession = {
      ...created,
      status: "completed",
      workflow_stage: "completed",
      current_summary: "Recommended: LangGraph. It scored 92/100."
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
      expect(screen.getByRole("button", { name: "Version 1 completed" })).toBeTruthy();
      expect(screen.getByText("GitHub repository health for LangGraph")).toBeTruthy();
      const traceRow = within(screen.getByLabelText("Evidence and trace"))
        .getByText("recommendation_generated")
        .closest("article");
      expect(traceRow).toBeTruthy();
      expect(
        within(traceRow as HTMLElement).getByText("Recommended: LangGraph. It scored 92/100.")
      ).toBeTruthy();
      expect(getRunButton().disabled).toBe(true);
      expect(screen.getByRole("button", { name: "Compare LangGraph completed" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Open evidence for LangGraph Runtime control and state/i }));
    await waitFor(() => {
      expect(within(screen.getByLabelText("Evidence drawer")).getByText(/strong runtime evidence/i))
        .toBeTruthy();
      expect(within(screen.getByLabelText("ADR")).getByText(/ADR v1/i)).toBeTruthy();
    });
  });

  it("shows Phase 3 risks, source filters, details, verification tasks, and skipped tools", async () => {
    const completed = makeSession("completed", "Recommended: LangGraph. It scored 92/100.");
    const workspace = makePhase3Workspace(completed);

    listSessionsMock.mockImplementation(async () => [completed]);
    getWorkspaceMock.mockImplementation(async () => workspace);

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));

    await waitFor(() => {
      const riskPanel = screen.getByLabelText("Risk panel");
      expect(within(riskPanel).getByText("confirmed")).toBeTruthy();
      expect(within(riskPanel).getByText("contradicted")).toBeTruthy();
      expect(within(riskPanel).getByText("unresolved")).toBeTruthy();
      expect(within(riskPanel).getByText("unverified")).toBeTruthy();
      expect(within(screen.getByLabelText("Tool calls")).getByText("phase3_reddit_search")).toBeTruthy();
      expect(within(screen.getByLabelText("Tool calls")).getByText("skipped")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Filter source reddit" }));
    await waitFor(() => {
      expect(within(screen.getByLabelText("Claims")).getByText("Release cadence is unresolved"))
        .toBeTruthy();
      expect(within(screen.getByLabelText("Claims")).queryByText("Checkpoint bug is confirmed"))
        .toBeNull();
      expect(within(screen.getByLabelText("Evidence list")).getByText("Community thread: stalled LangGraph releases"))
        .toBeTruthy();
      expect(within(screen.getByLabelText("Evidence list")).queryByText("GitHub issue: stale checkpoint bug"))
        .toBeNull();
    });

    fireEvent.click(screen.getByRole("button", { name: "Filter source all" }));
    fireEvent.click(screen.getByRole("button", { name: "Open risk Checkpoint staleness" }));
    await waitFor(() => {
      const detail = screen.getByLabelText("Risk detail");
      expect(within(detail).getByText("Checkpoint bug is confirmed")).toBeTruthy();
      expect(within(detail).getByText(/Maintainers confirmed stale checkpoint state/i)).toBeTruthy();
      expect(within(detail).getByText("GitHub issue: stale checkpoint bug")).toBeTruthy();
      expect(within(detail).getByText("Does stronger evidence confirm checkpoint staleness?")).toBeTruthy();
    });
  });

  it("updates Phase 2 draft controls and shows gap analysis without creating a version", async () => {
    const completed = makeSession("completed", "Recommended: LangGraph. It scored 92/100.");
    let workspace = makeCompletedWorkspace(completed);

    listSessionsMock.mockImplementation(async () => [completed]);
    getWorkspaceMock.mockImplementation(async () => workspace);
    updateDraftMock.mockImplementation(async (_sessionId: string, draft: Phase2Draft) => {
      workspace = makeCompletedWorkspace(completed, {
        draft,
        gap_analysis: gapFromDraft(draft)
      });
      return workspace;
    });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Version 1 completed" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Must exclude LangGraph" }));
    await waitFor(() => {
      expect(updateDraftMock).toHaveBeenLastCalledWith(
        completed.id,
        expect.objectContaining({
          candidate_overrides: expect.objectContaining({
            langgraph: expect.objectContaining({ action: "must_exclude" })
          })
        })
      );
      expect(within(screen.getByLabelText("Gap analysis")).getByText(/Changed candidates: langgraph/i))
        .toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Must include checkpointing" }));
    await waitFor(() => {
      expect(updateDraftMock).toHaveBeenLastCalledWith(
        completed.id,
        expect.objectContaining({
          must_include_constraints: expect.objectContaining({
            checkpointing: expect.objectContaining({ enabled: true })
          })
        })
      );
      expect(within(screen.getByLabelText("Gap analysis")).getByText(/Changed constraints: checkpointing/i))
        .toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("Custom candidate name"), {
      target: { value: "AutoGen" }
    });
    fireEvent.change(screen.getByLabelText("Custom candidate slug"), {
      target: { value: "autogen" }
    });
    fireEvent.change(screen.getByLabelText("Custom candidate repository"), {
      target: { value: "microsoft/autogen" }
    });
    fireEvent.change(screen.getByLabelText("Custom candidate reason"), {
      target: { value: "Team wants to compare another agent framework." }
    });
    fireEvent.click(screen.getByRole("button", { name: "Add custom candidate" }));
    await waitFor(() => {
      expect(updateDraftMock).toHaveBeenLastCalledWith(
        completed.id,
        expect.objectContaining({
          custom_candidates: expect.objectContaining({
            autogen: expect.objectContaining({ repo_full_name: "microsoft/autogen" })
          })
        })
      );
    });

    fireEvent.change(screen.getByLabelText("Weight for Runtime control and state"), {
      target: { value: "45" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply Runtime control and state weight" }));
    await waitFor(() => {
      expect(updateDraftMock).toHaveBeenLastCalledWith(
        completed.id,
        expect.objectContaining({
          weight_overrides: expect.objectContaining({
            "Runtime control and state": expect.objectContaining({ weight: 45 })
          })
        })
      );
      expect(within(screen.getByLabelText("Gap analysis")).getByText(/Changed weights: Runtime control and state/i))
        .toBeTruthy();
    });

    expect(createDecisionVersionMock).not.toHaveBeenCalled();
  });

  it("keeps saved weight override visible when a completed session becomes context_changed", async () => {
    const completed = makeSession("completed", "Recommended: LangGraph. It scored 92/100.");
    const contextChanged: DecisionSession = {
      ...completed,
      workflow_stage: "context_changed"
    };
    const workspace = makeCompletedWorkspace(completed);

    listSessionsMock.mockImplementation(async () => [completed]);
    getWorkspaceMock.mockImplementation(async () => workspace);
    updateDraftMock.mockImplementation(async (_sessionId: string, draft: Phase2Draft) =>
      makeCompletedWorkspace(contextChanged, {
        draft,
        gap_analysis: gapFromDraft(draft)
      })
    );

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Version 1 completed" })).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText("Weight for Runtime control and state"), {
      target: { value: "45" }
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply Runtime control and state weight" }));

    await waitFor(() => {
      expect(within(screen.getByLabelText("Decision workspace")).getByText("context_changed")).toBeTruthy();
      expect(
        (screen.getByLabelText("Weight for Runtime control and state") as HTMLInputElement).value
      ).toBe("45");
    });
  });

  it("does not overwrite an in-progress weight edit when polling refreshes the workspace", async () => {
    const completed = makeSession("completed", "Recommended: LangGraph. It scored 92/100.");
    const workspace = makeCompletedWorkspace(completed);

    listSessionsMock.mockImplementation(async () => [completed]);
    getWorkspaceMock.mockImplementation(async () => workspace);

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Weight for Runtime control and state") as HTMLInputElement).value
      ).toBe("30");
    });

    fireEvent.change(screen.getByLabelText("Weight for Runtime control and state"), {
      target: { value: "45" }
    });
    expect(
      (screen.getByLabelText("Weight for Runtime control and state") as HTMLInputElement).value
    ).toBe("45");

    await runPoll();

    await waitFor(() => {
      expect(
        (screen.getByLabelText("Weight for Runtime control and state") as HTMLInputElement).value
      ).toBe("45");
    });
    expect(updateDraftMock).not.toHaveBeenCalled();
  });

  it("ignores older same-session draft responses when draft saves resolve out of order", async () => {
    const completed = makeSession("completed", "Recommended: LangGraph. It scored 92/100.");
    let workspace = makeCompletedWorkspace(completed);
    const firstDraftRequest = deferred<Workspace>();
    const secondDraftRequest = deferred<Workspace>();
    const draftResponses: Workspace[] = [];

    listSessionsMock.mockImplementation(async () => [completed]);
    getWorkspaceMock.mockImplementation(async () => workspace);
    updateDraftMock.mockImplementation(async (_sessionId: string, draft: Phase2Draft) => {
      const nextWorkspace = makeCompletedWorkspace(completed, {
        draft,
        gap_analysis: gapFromDraft(draft)
      });
      draftResponses.push(nextWorkspace);
      if (draftResponses.length === 1) return firstDraftRequest.promise;
      if (draftResponses.length === 2) return secondDraftRequest.promise;
      return nextWorkspace;
    });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Must exclude LangGraph" })).toBeTruthy();
    });

    act(() => {
      fireEvent.click(screen.getByRole("button", { name: "Must exclude LangGraph" }));
      fireEvent.click(screen.getByRole("button", { name: "Must include checkpointing" }));
    });
    await waitFor(() => {
      expect(updateDraftMock).toHaveBeenCalledTimes(2);
    });

    workspace = draftResponses[1];
    await act(async () => {
      secondDraftRequest.resolve(draftResponses[1]);
      await secondDraftRequest.promise;
    });
    await waitFor(() => {
      expect(within(screen.getByLabelText("Gap analysis")).getByText(/Changed constraints: checkpointing/i))
        .toBeTruthy();
    });

    await act(async () => {
      firstDraftRequest.resolve(draftResponses[0]);
      await firstDraftRequest.promise;
    });
    await waitFor(() => {
      expect(within(screen.getByLabelText("Gap analysis")).getByText(/Changed constraints: checkpointing/i))
        .toBeTruthy();
      expect(within(screen.getByLabelText("Gap analysis")).queryByText(/Changed candidates: langgraph/i))
        .toBeNull();
    });
  });

  it("creates a Phase 2 version only after draft changes exist", async () => {
    const completed = makeSession("completed", "Recommended: LangGraph. It scored 92/100.");
    const queued: DecisionSession = { ...completed, status: "queued", workflow_stage: "queued" };
    let workspace = makeCompletedWorkspace(completed);

    listSessionsMock.mockImplementation(async () => [workspace.session]);
    getWorkspaceMock.mockImplementation(async () => workspace);
    updateDraftMock.mockImplementation(async (_sessionId: string, draft: Phase2Draft) => {
      workspace = makeCompletedWorkspace(completed, {
        draft,
        gap_analysis: gapFromDraft(draft)
      });
      return workspace;
    });
    createDecisionVersionMock.mockImplementation(async () => makeRunResponse(queued));

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(getTargetedRunButton().disabled).toBe(true);
    });

    fireEvent.click(screen.getByRole("button", { name: "Exclude LangGraph" }));
    await waitFor(() => {
      expect(getTargetedRunButton().disabled).toBe(false);
    });
    fireEvent.click(screen.getByRole("button", { name: "Run targeted re-research" }));
    await waitFor(() => {
      expect(createDecisionVersionMock).toHaveBeenCalledWith(completed.id);
    });
  });

  it("follows the latest completed version after targeted re-research", async () => {
    const completedV1 = makeSession("completed", "Recommended: LangGraph. It scored 92/100.");
    const queued: DecisionSession = { ...completedV1, status: "queued", workflow_stage: "queued" };
    const completedV2: DecisionSession = {
      ...completedV1,
      status: "completed",
      workflow_stage: "completed",
      current_summary: "Recommended v2: LangGraph."
    };
    const versionOne = makeVersion(1, { session_id: completedV1.id });
    const versionTwoQueued = makeVersion(2, {
      session_id: completedV1.id,
      status: "queued",
      completed_at: null
    });
    const versionTwo = makeVersion(2, {
      session_id: completedV1.id,
      adr: "ADR 0002: Recommended v2: LangGraph."
    });
    const v1Workspace = makeCompletedWorkspace(completedV1, {
      versions: [versionOne],
      active_version: versionOne,
      draft: makeDraft(versionOne.id)
    });
    const queuedWorkspace = makeCompletedWorkspace(queued, {
      versions: [versionOne, versionTwoQueued],
      active_version: versionOne,
      draft: makeDraft(versionOne.id, {
        candidate_overrides: {
          langgraph: { action: "exclude", reason: "Recheck alternatives." }
        }
      })
    });
    const v2Workspace = makeCompletedWorkspace(completedV2, {
      versions: [versionOne, versionTwo],
      active_version: versionTwo,
      draft: makeDraft(versionTwo.id)
    });
    let workspace = v1Workspace;

    listSessionsMock.mockImplementation(async () => [workspace.session]);
    getWorkspaceMock.mockImplementation(async (_sessionId: string, versionId?: string) => {
      if (versionId === versionOne.id) return v1Workspace;
      if (versionId === versionTwo.id) return v2Workspace;
      return workspace;
    });
    updateDraftMock.mockImplementation(async (_sessionId: string, draft: Phase2Draft) => {
      workspace = makeCompletedWorkspace(completedV1, {
        versions: [versionOne],
        active_version: versionOne,
        draft,
        gap_analysis: gapFromDraft(draft)
      });
      return workspace;
    });
    createDecisionVersionMock.mockImplementation(async () => {
      workspace = queuedWorkspace;
      return makeRunResponse(queued);
    });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Version 1 completed" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Exclude LangGraph" }));
    await waitFor(() => {
      expect(getTargetedRunButton().disabled).toBe(false);
    });
    fireEvent.click(screen.getByRole("button", { name: "Run targeted re-research" }));
    await waitFor(() => {
      expect(createDecisionVersionMock).toHaveBeenCalledWith(completedV1.id);
    });
    await waitFor(() => {
      expect(within(screen.getByLabelText("ADR")).getByText(/ADR v1/i)).toBeTruthy();
    });

    workspace = v2Workspace;
    await runPoll();

    await waitFor(() => {
      expect(within(screen.getByLabelText("ADR")).getByText(/ADR 0002/i)).toBeTruthy();
      expect(screen.getByRole("button", { name: "Version 2 completed" })).toBeTruthy();
    });
  });

  it("changes ADR with the selected version", async () => {
    const completed = makeSession("completed", "Recommended: OpenAI Agents SDK.");
    const versionOne = makeVersion(1, { session_id: completed.id });
    const versionTwo = makeVersion(2, { session_id: completed.id });
    const baseWorkspace = makeCompletedWorkspace(completed, {
      versions: [versionOne, versionTwo],
      active_version: versionTwo,
      draft: makeDraft(versionTwo.id)
    });
    const versionOneWorkspace = makeCompletedWorkspace(completed, {
      versions: [versionOne, versionTwo],
      active_version: versionOne,
      draft: makeDraft(versionOne.id)
    });
    const versionTwoWorkspace = makeCompletedWorkspace(completed, {
      versions: [versionOne, versionTwo],
      active_version: versionTwo,
      draft: makeDraft(versionTwo.id)
    });

    listSessionsMock.mockImplementation(async () => [completed]);
    getWorkspaceMock.mockImplementation(async (_sessionId: string, versionId?: string) => {
      if (versionId === versionOne.id) return versionOneWorkspace;
      if (versionId === versionTwo.id) return versionTwoWorkspace;
      return baseWorkspace;
    });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(within(screen.getByLabelText("ADR")).getByText(/ADR v2/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Version 1 completed" }));
    await waitFor(() => {
      expect(within(screen.getByLabelText("ADR")).getByText(/ADR v1/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Version 2 completed" }));
    await waitFor(() => {
      expect(within(screen.getByLabelText("ADR")).getByText(/ADR v2/i)).toBeTruthy();
    });
  });

  it("keeps current version surfaces while a requested version workspace is loading", async () => {
    const completed = makeSession("completed", "Recommended: OpenAI Agents SDK.");
    const versionOne = makeVersion(1, { session_id: completed.id });
    const versionTwo = makeVersion(2, { session_id: completed.id });
    const versionOneRequest = deferred<Workspace>();
    const baseWorkspace = makeCompletedWorkspace(completed, {
      versions: [versionOne, versionTwo],
      active_version: versionTwo,
      draft: makeDraft(versionTwo.id)
    });
    const versionOneWorkspace = makeCompletedWorkspace(completed, {
      versions: [versionOne, versionTwo],
      active_version: versionOne,
      draft: makeDraft(versionOne.id)
    });

    listSessionsMock.mockImplementation(async () => [completed]);
    getWorkspaceMock.mockImplementation(async (_sessionId: string, versionId?: string) => {
      if (versionId === versionOne.id) return versionOneRequest.promise;
      return baseWorkspace;
    });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(within(screen.getByLabelText("ADR")).getByText(/ADR v2/i)).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Version 1 completed" }));
    expect(within(screen.getByLabelText("ADR")).getByText(/ADR v2/i)).toBeTruthy();
    await runPoll();
    expect(within(screen.getByLabelText("ADR")).getByText(/ADR v2/i)).toBeTruthy();

    await act(async () => {
      versionOneRequest.resolve(versionOneWorkspace);
      await versionOneRequest.promise;
    });
    await waitFor(() => {
      expect(within(screen.getByLabelText("ADR")).getByText(/ADR v1/i)).toBeTruthy();
    });
  });

  it("keeps the selected version when version workspace requests resolve out of order", async () => {
    const completed = makeSession("completed", "Recommended: OpenAI Agents SDK.");
    const versionOne = makeVersion(1, { session_id: completed.id });
    const versionTwo = makeVersion(2, { session_id: completed.id });
    const firstVersionRequest = deferred<Workspace>();
    const secondVersionRequest = deferred<Workspace>();
    const baseWorkspace = makeCompletedWorkspace(completed, {
      versions: [versionOne, versionTwo],
      active_version: versionTwo,
      draft: makeDraft(versionTwo.id)
    });

    listSessionsMock.mockImplementation(async () => [completed]);
    getWorkspaceMock.mockImplementation(async (_sessionId: string, versionId?: string) => {
      if (versionId === versionOne.id) return firstVersionRequest.promise;
      if (versionId === versionTwo.id) return secondVersionRequest.promise;
      return baseWorkspace;
    });

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Version 1 completed" })).toBeTruthy();
      expect(screen.getByRole("button", { name: "Version 2 completed" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Version 1 completed" }));
    fireEvent.click(screen.getByRole("button", { name: "Version 2 completed" }));

    await act(async () => {
      secondVersionRequest.resolve(
        makeCompletedWorkspace(completed, {
          versions: [versionOne, versionTwo],
          active_version: versionTwo,
          draft: makeDraft(versionTwo.id)
        })
      );
      await secondVersionRequest.promise;
    });
    await waitFor(() => {
      expect(within(screen.getByLabelText("ADR")).getByText(/ADR v2/i)).toBeTruthy();
    });

    await act(async () => {
      firstVersionRequest.resolve(
        makeCompletedWorkspace(completed, {
          versions: [versionOne, versionTwo],
          active_version: versionOne,
          draft: makeDraft(versionOne.id)
        })
      );
      await firstVersionRequest.promise;
    });

    await waitFor(() => {
      expect(within(screen.getByLabelText("ADR")).getByText(/ADR v2/i)).toBeTruthy();
      expect(within(screen.getByLabelText("ADR")).queryByText(/ADR v1/i)).toBeNull();
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

  it("keeps a clarification response when an older same-session workspace load resolves later", async () => {
    const clarifying = makeSession("created");
    const ready: DecisionSession = {
      ...clarifying,
      workflow_stage: "ready_for_research"
    };
    const workspaceRequest = deferred<Workspace>();

    listSessionsMock.mockImplementation(async () => [clarifying]);
    getWorkspaceMock.mockImplementation(async (sessionId: string) => {
      if (sessionId === clarifying.id) return workspaceRequest.promise;
      throw new Error(`Unexpected workspace request: ${sessionId}`);
    });
    sendMessageMock.mockImplementation(async () =>
      makeWorkspace(ready, [{ role: "assistant", content: "Ready workspace marker" }])
    );

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    fireEvent.change(screen.getByLabelText("Clarification answer"), {
      target: { value: "Python and checkpointing matter most." }
    });
    await waitFor(() => {
      expect(getSendAnswerButton().disabled).toBe(false);
    });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));

    await waitFor(() => {
      expect(within(screen.getByLabelText("Decision workspace")).getByText("ready_for_research"))
        .toBeTruthy();
      expect(screen.getByText("Ready workspace marker")).toBeTruthy();
      expect(getRunButton().disabled).toBe(false);
    });

    await act(async () => {
      workspaceRequest.resolve(
        makeWorkspace(clarifying, [{ role: "assistant", content: "Stale workspace marker" }])
      );
      await workspaceRequest.promise;
    });

    await waitFor(() => {
      expect(within(screen.getByLabelText("Decision workspace")).getByText("ready_for_research"))
        .toBeTruthy();
      expect(screen.getByText("Ready workspace marker")).toBeTruthy();
      expect(screen.queryByText("Stale workspace marker")).toBeNull();
      expect(getRunButton().disabled).toBe(false);
    });
  });

  it("keeps a run response when an older same-session workspace load resolves before refresh", async () => {
    const ready: DecisionSession = {
      ...makeSession("created"),
      workflow_stage: "ready_for_research"
    };
    const queued: DecisionSession = {
      ...ready,
      status: "queued",
      workflow_stage: "queued"
    };
    const initialWorkspaceRequest = deferred<Workspace>();
    const staleWorkspaceRequest = deferred<Workspace>();
    const refreshSessionsRequest = deferred<DecisionSession[]>();
    let workspaceRequestCount = 0;
    let sessionListCount = 0;

    listSessionsMock.mockImplementation(async () => {
      sessionListCount += 1;
      if (sessionListCount === 1) return [ready];
      return refreshSessionsRequest.promise;
    });
    getWorkspaceMock.mockImplementation(async (sessionId: string) => {
      if (sessionId !== ready.id) {
        throw new Error(`Unexpected workspace request: ${sessionId}`);
      }
      workspaceRequestCount += 1;
      if (workspaceRequestCount === 1) return initialWorkspaceRequest.promise;
      if (workspaceRequestCount === 2) return staleWorkspaceRequest.promise;
      return makeWorkspace(queued, [{ role: "assistant", content: "Queued workspace marker" }]);
    });
    startRunMock.mockImplementation(async () => makeRunResponse(queued));

    render(<App />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Compare LangGraph/ })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    await act(async () => {
      initialWorkspaceRequest.resolve(
        makeWorkspace(ready, [{ role: "assistant", content: "Ready workspace marker" }])
      );
      await initialWorkspaceRequest.promise;
    });
    await waitFor(() => {
      expect(within(screen.getByLabelText("Decision workspace")).getByText("ready_for_research"))
        .toBeTruthy();
      expect(getRunButton().disabled).toBe(false);
    });

    fireEvent.click(screen.getByRole("button", { name: /Compare LangGraph/ }));
    fireEvent.click(screen.getByRole("button", { name: "Run Phase 1 Agent" }));
    await waitFor(() => {
      expect(within(screen.getByLabelText("Decision workspace")).getByText("queued")).toBeTruthy();
    });

    await act(async () => {
      staleWorkspaceRequest.resolve(
        makeWorkspace(ready, [{ role: "assistant", content: "Stale run workspace marker" }])
      );
      await staleWorkspaceRequest.promise;
    });

    await waitFor(() => {
      expect(within(screen.getByLabelText("Decision workspace")).getByText("queued")).toBeTruthy();
      expect(screen.queryByText("Stale run workspace marker")).toBeNull();
    });

    await act(async () => {
      refreshSessionsRequest.resolve([queued]);
      await refreshSessionsRequest.promise;
    });
  });
});
