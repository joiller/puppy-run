# PuppyRun Design

Date: 2026-05-21

## 1. Product Positioning

PuppyRun is a web-based Agent operating platform for AI Agent technology stack selection.

The product helps developers make auditable, evidence-grounded technical decisions. A user can start with a natural-language question such as:

> I want to build a web Agent operating platform. Should I use LangGraph, OpenAI Agents SDK, CrewAI, AutoGen, Dify, Flowise, or build my own runtime?

The platform does not immediately generate a static report. Instead, it runs a structured Agent workflow:

1. Clarify the user's project context through multi-turn dialogue.
2. Extract explicit constraints such as must-include and must-exclude technologies.
3. Discover candidate open-source technologies automatically.
4. Generate evaluation criteria and weights from the user's context.
5. Collect evidence from official docs, public GitHub repositories, papers, technical blogs, and community discussions.
6. Verify community risk signals through higher-trust sources before they affect scoring.
7. Build an interactive evidence matrix, recommendation, risk analysis, and ADR view.
8. Let the user adjust constraints, candidates, or weights and trigger targeted re-research.

The core differentiator is not "AI writes a report." The system turns technical decision-making into a versioned, inspectable, replayable Agent workflow.

## 2. First-Version Scope

The first version focuses on AI Agent technology stack selection, especially open-source Agent frameworks and related infrastructure.

Primary examples:

- LangGraph vs CrewAI vs OpenAI Agents SDK vs Microsoft Agent Framework / AutoGen.
- Agent observability tools such as Langfuse, Phoenix, OpenTelemetry, and Opik.
- RAG and knowledge tools such as LlamaIndex, LangChain, and Haystack.
- Workflow and long-task infrastructure such as Temporal, Celery, BullMQ, and framework-native checkpointing.

The initial demo should focus on Agent orchestration framework selection. This fits the job-seeking goal because it naturally exposes Agent architecture, tool calling, state persistence, human-in-the-loop design, observability, evaluation, and backend integration concerns.

## 3. Non-Goals For MVP

The MVP should not become a generic low-code Agent platform or a generic Deep Research clone.

Out of scope for the first version:

- Private GitHub repository access.
- Full GitHub OAuth and organization permission management.
- Multi-user real-time collaboration.
- Complex plugin marketplace.
- Enterprise RBAC.
- Payment and billing.
- Large-scale crawling.
- Full benchmark execution for every candidate technology.
- Default PDF, Markdown, or HTML export.

Exports are only generated when the user explicitly asks for them, when project instructions require them, or when a relevant workflow requires a saved artifact.

## 4. Core User Flow

### 4.1 Create Decision Session

The user starts with a free-form question. The system creates a `decision_session` and enters the `clarifying` state.

### 4.2 Multi-Turn Clarification

The Clarification Agent evaluates whether the current decision context is sufficient. On each turn it:

- Updates the structured decision context.
- Identifies missing information.
- Explains why the missing information matters.
- Asks the next most important 1-3 questions.
- Decides whether the workflow can move forward.

Clarification is not a one-time form. It is a stateful Agent capability.

### 4.3 Candidate Discovery

The Candidate Discovery Agent can operate in three modes:

- User-specified candidates.
- Agent-discovered candidates.
- Mixed mode, where the user provides some candidates and the Agent adds missing ones.

The Agent must support:

- Must include.
- Must exclude.
- Ecosystem constraints such as Python or TypeScript.
- License constraints.
- Deployment constraints.
- Capability constraints such as tool calling, checkpointing, human-in-the-loop, and tracing.
- Risk constraints such as excluding inactive projects.

Every included or excluded candidate should have an explanation.

### 4.4 Criteria And Weight Generation

The Criteria Agent automatically generates evaluation criteria and initial weights from the decision context.

Each criterion should include:

- Name.
- Weight.
- Why it matters.
- Which user constraint or project goal caused it.
- What evidence is needed.
- Which candidates it affects.

The user can edit weights, disable criteria, or add new criteria.

### 4.5 Research And Evidence Collection

The Research Agent plans and executes evidence collection. It uses tools for:

- Official documentation.
- Public GitHub repository analysis.
- Release and issue analysis.
- Papers and arXiv.
- Technical blogs.
- Hacker News, Reddit, Stack Overflow, and Stack Exchange style discussions.

Community sources can influence the Agent's suspicion and research direction, but they should not directly dominate scoring without verification.

### 4.6 Community Risk Verification

Community discussions are processed as signals:

1. Extract claims from community content.
2. Cluster related claims into `risk_signals`.
3. Generate `verification_tasks`.
4. Verify through official docs, GitHub issues, releases, source examples, or credible technical writeups.
5. Classify each signal as confirmed, contradicted, or unresolved.
6. Apply score impact with an explanation.

### 4.7 Interactive Decision Workbench

The final result lives in the web platform, not as a static report by default.

The workbench includes:

- Decision context.
- Clarification history.
- Candidate pool.
- Criteria and weights.
- Evidence matrix.
- GitHub health summary.
- Risk panel.
- Recommendation.
- ADR view.
- Decision version history.
- Agent trace and replay.

### 4.8 Incremental Re-Research

When the user changes constraints, weights, or candidates, the system should not blindly rerun the whole workflow.

It should:

1. Run gap analysis.
2. Identify which evidence is invalid, missing, or insufficient.
3. Propose targeted research tasks.
4. Ask the user to confirm before spending additional API or LLM budget.
5. Generate a new `decision_version`.

## 5. Page And Interaction Design

The app should be a work-focused web console, not a landing page.

Main pages:

- Dashboard: list decision sessions by status.
- New Decision: create a new decision session from free-form input.
- Decision Workspace: main working surface.
- Run Trace: inspect Agent runs and tool calls.
- Evidence Library: inspect collected evidence and source metadata.
- Settings: model, API source, public GitHub rate-limit status, rate limits, export preferences.

Decision Workspace layout:

- Left: session outline, current state, decision brief, candidates, criteria, versions.
- Center: current stage, including clarification, candidate confirmation, matrix, or recommendation.
- Right: evidence and trace drawer.
- Top or bottom: run status bar showing current Agent action.

Important interactions:

- Confirm or edit candidate pool.
- Adjust criteria weights.
- Open evidence for each matrix cell.
- Inspect why a candidate was included or excluded.
- Inspect why a score changed.
- Trigger targeted re-research after context changes.
- Compare decision versions.

## 6. Agent Architecture

The system uses multiple specialized Agents over a shared runtime:

- Orchestrator Agent: controls state transitions and selects the next workflow action.
- Clarification Agent: detects missing decision context and asks follow-up questions.
- Candidate Discovery Agent: discovers and filters candidate technologies.
- Criteria Agent: generates evaluation dimensions and weights.
- Research Agent: plans and collects evidence.
- Signal Verification Agent: verifies community risk signals.
- Decision Synthesis Agent: generates scores, recommendation, risk analysis, and ADR view.

This decomposition is used because each stage has different inputs, outputs, evaluation needs, and trace requirements.

## 7. Tool Runtime And MCP

The platform should have a unified Tool Runtime. MCP is supported as one adapter type, not as the whole tool system.

Tool architecture:

```text
Agent
  -> Tool Runtime
    -> Tool Adapter Layer
      -> Built-in Tools
      -> REST API Tools
      -> MCP Tools
      -> Internal Platform Tools
```

Tool Runtime responsibilities:

- Tool registry.
- Typed input and output schemas.
- Schema validation.
- Permission checks.
- Timeout and retry.
- Idempotency key.
- Rate limit handling.
- Tool call trace.
- Result normalization into evidence-friendly structures.

First-version tool categories:

- Web search tool.
- Official docs tool.
- GitHub repo analyzer tool.
- Paper / arXiv tool.
- Blog search tool.
- Community signal tool.
- Internal scoring and versioning tools.

MCP should be demonstrated through one or two adapters where useful, while core tools can remain built-in or REST-based for reliability.

## 8. Backend Architecture

Use a modular monolith with asynchronous workers. Avoid premature microservices.

Core components:

- API Server.
- Agent Runtime.
- Worker Queue.
- PostgreSQL database.
- Redis for queue, cache, and rate limits.
- Realtime channel through SSE or WebSocket.
- Tool Registry.
- Eval Runner.
- Observability and logging.

Key backend concerns:

- Long-running Agent state machine.
- Async worker execution.
- Idempotent tool calls.
- Retry and dead-letter handling.
- Evidence persistence.
- Decision versioning.
- Audit log.
- API and LLM cost limits.
- External API rate limits.
- Prompt-injection handling for external content.
- Workspace isolation.

## 9. Core Data Model

Important tables:

- `users`
- `workspaces`
- `decision_sessions`
- `decision_messages`
- `decision_constraints`
- `decision_candidates`
- `decision_criteria`
- `evidence_items`
- `claims`
- `risk_signals`
- `verification_tasks`
- `score_cells`
- `recommendations`
- `decision_versions`
- `agent_runs`
- `agent_events`
- `tool_calls`
- `tool_registry`
- `audit_events`
- `eval_runs`

High-level relationship:

```text
workspace
  -> decision_session
    -> decision_messages
    -> decision_constraints
    -> decision_candidates
    -> decision_criteria
    -> evidence_items
      -> claims
    -> risk_signals
      -> verification_tasks
    -> score_cells
    -> recommendations
    -> decision_versions
    -> agent_runs
      -> agent_events
      -> tool_calls
```

## 10. Decision Session States

Primary states:

```text
created
clarifying
discovering_candidates
confirming_candidates
generating_criteria
confirming_criteria
researching
verifying_signals
comparing
ready_for_review
completed
failed
cancelled
```

Context-change states:

```text
context_changed
gap_analysis
targeted_research
recomparing
new_version_ready
```

## 11. Evidence Model

`evidence_items` should store structured source metadata:

- Source type.
- Source URL.
- Candidate.
- Related criterion.
- Extracted claim.
- Credibility level.
- Freshness.
- Confidence.
- Retrieved timestamp.
- Raw snapshot hash.
- Summary.
- Citation text.

Source credibility tiers:

- High: official docs, source code, official releases.
- Medium: GitHub issues, PRs, technical blogs, papers, benchmarks.
- Low: Reddit, Hacker News, Stack Overflow comments, community discussion.

Low-trust evidence can generate risk signals, but strong scoring impact should require verification.

## 12. Evaluation System

The first version should include a small eval suite with 10-20 curated cases.

Eval categories:

- Clarification Eval: did the Agent ask important missing questions?
- Candidate Discovery Eval: did it find reasonable candidates and avoid irrelevant ones?
- Evidence Grounding Eval: are claims supported by evidence?
- Risk Verification Eval: are community risks verified correctly?
- Decision Consistency Eval: does the recommendation change reasonably when constraints change?

Use a combination of:

- Rule-based checks.
- LLM-as-judge for qualitative judgment.
- Manually curated golden cases.
- Regression evals after prompt, tool, or scoring changes.

The UI should include a small eval dashboard showing:

- Eval run history.
- Category-level scores.
- Failed cases.
- Tool error rate.
- Average latency.
- LLM cost.
- Evidence grounding rate.
- Unsupported claim count.

## 13. Deployment Strategy

Deployment is an early constraint, not a final cleanup task.

The project should support:

- Public online demo.
- Docker Compose self-host mode.
- Health checks.
- Database migrations.
- Worker process.
- Redis queue.
- Basic auth or demo user.
- Workspace isolation.
- Session quotas.
- Rate limits.
- LLM cost budget.
- Logs and basic metrics.
- Seed demo data.

The app should be usable online as early as possible.

## 14. Revised Phase Plan

### Phase 0: Deployable Skeleton

Goal: create an online-ready architecture before implementing complex Agent behavior.

Scope:

- Frontend shell.
- API server.
- PostgreSQL.
- Redis / queue.
- Worker process.
- Basic auth or demo user.
- Health check.
- Docker Compose.
- CI build.
- Production environment.
- Dummy Agent job that updates session state.

Success criteria:

- A public URL can load the app.
- A user can create a session.
- A background worker can process a dummy Agent job.
- The frontend receives updated status.

### Phase 1: Online Thin Slice

Goal: ship the smallest real Agent workflow online.

Scope:

- Free-form decision input.
- 1-2 clarification turns.
- Candidate discovery for Agent frameworks.
- Criteria generation.
- Public GitHub analysis for 2-3 candidates.
- Basic evidence summary.
- Basic recommendation.
- Agent trace.

Success criteria:

- The platform can answer one realistic question about selecting an Agent framework for a web Agent runtime project.

### Phase 2: Interactive Workbench

Goal: make the result adjustable and versioned.

Scope:

- Candidate add/remove/lock.
- Must include / must exclude.
- Weight editing.
- Evidence drawer.
- Evidence matrix.
- Decision versions.
- Gap analysis.
- Targeted re-research.
- ADR view.

Success criteria:

- Changing a constraint such as "must support checkpoint and human-in-the-loop" triggers targeted re-research and creates a new recommendation version.

### Phase 3: Evidence And Risk Verification

Goal: distinguish the product from Deep Research-style static reports.

Scope:

- Official docs tool.
- Blog and paper search.
- arXiv integration.
- HN and Stack Exchange integrations.
- Reddit integration only when OAuth and API policy requirements are configured explicitly.
- Claim extraction.
- Risk signal clustering.
- Verification tasks.
- Credibility scoring.

Success criteria:

- Community complaints become risk signals, are verified through stronger evidence, and affect the risk panel and scores with explicit reasoning.

### Phase 4: Eval And Observability

Goal: prove Agent output quality and operational quality.

Scope:

- Curated eval cases.
- Clarification eval.
- Candidate discovery eval.
- Evidence grounding eval.
- Risk verification eval.
- Decision consistency eval.
- Eval dashboard.
- Tool error rate, cost, latency, and queue metrics.

Success criteria:

- Prompt, tool, or scoring changes can be checked against regression evals.

### Phase 5: Production Hardening

Goal: make the deployed project credible for interview review.

Scope:

- Stronger quotas.
- Rate limiting.
- Admin view.
- Cost budget enforcement.
- Monitoring and alerting.
- Better demo seed data.
- Documentation.
- Security hardening.
- Optional export jobs.

Success criteria:

- The online demo is stable enough for a recruiter or interviewer to try, and the repository can be self-hosted with documented setup.

## 15. Resume Narrative

Possible resume title:

PuppyRun: auditable AI Agent technology stack selection platform.

Resume bullets:

- Designed and implemented a web Agent operating platform for AI Agent technology stack selection, supporting multi-turn clarification, candidate discovery, criteria generation, evidence matrix, and ADR versioning.
- Built a unified Agent Runtime and Tool Runtime supporting built-in tools, REST tools, and MCP adapters with schema validation, permission checks, timeout/retry, idempotency keys, and tool call traces.
- Implemented public GitHub repository analysis for README, releases, issues, PRs, contributors, license, and documentation quality to support open-source technology health scoring.
- Designed a community risk verification loop that extracts risk signals from HN, Reddit, Stack Overflow, and technical discussions, then verifies them through official docs, GitHub issues, releases, and credible engineering writeups before affecting scores.
- Built decision versioning and targeted re-research so changes to constraints, weights, or candidates trigger gap analysis and generate new recommendation versions.
- Added Agent evals for clarification quality, candidate discovery, evidence grounding, risk verification, and decision consistency, with metrics for tool errors, cost, latency, and unsupported claims.
- Deployed the app as a public online demo and provided Docker Compose self-host mode with API server, worker, PostgreSQL, Redis, health checks, quotas, and rate limits.

## 16. Open Risks

- Search and community APIs can be rate-limited or unstable.
- Reddit API access may require OAuth and policy care.
- Web search quality can affect candidate discovery quality.
- Community discussions are noisy and need careful credibility handling.
- LLM cost can grow quickly during research and verification.
- Too many sources in MVP can slow delivery; the online thin slice must remain small.
- The UI must avoid becoming a generic chat interface.
- The platform must avoid looking like a Dify or Deep Research clone.

## 17. Approval Status

The high-level direction has been approved:

- Web Agent operating platform.
- AI Agent technology stack selection as the first-version scenario.
- Interactive decision workbench rather than static report.
- Agent-generated candidates, criteria, and weights.
- Multi-turn clarification instead of one-time forms.
- Public GitHub repository analysis in the first version.
- Open-source technologies as the main target.
- Community sources included through a verification loop.
- Deployment moved early through Phase 0 and Phase 1.

