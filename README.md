# PuppyRun

PuppyRun is an agentic, evidence-grounded workbench for technical stack and architecture decisions.

The first demo workflow focuses on AI Agent technology stack selection. The first client is a web console, while the core decision workflow is designed to be reusable by future clients such as desktop, mobile, CLI, or IDE integrations.

Phase 1 adds the first real online Agent workflow: a deterministic Agent-framework selection thin slice with clarification, candidate discovery, criteria generation, public GitHub repository analysis, a basic recommendation, and trace events.

Phase 2 adds the interactive decision workbench: versioned recommendations, explicit candidate and constraint controls, criteria weight editing, pre-rerun gap analysis, targeted GitHub-only re-research, score cells, an evidence drawer, and ADR views.

Phase 3 adds evidence and risk verification: versioned tool-call traces, source evidence, extracted claims, risk signals, verification tasks, conservative confirmed-risk score adjustments, risk-aware recommendation/ADR output, and workbench panels for risk details and source filtering.

To increase GitHub API rate limits in a public deployment, set `PUPPYRUN_GITHUB_TOKEN` in the deployment environment. The token is optional for local smoke tests and must not be committed.

Phase 3 uses `PUPPYRUN_LLM_PROVIDER=deterministic` by default, so no-key local and Docker smoke tests do not make live LLM calls. Optional live extraction can use `PUPPYRUN_LLM_PROVIDER=openai` with `PUPPYRUN_OPENAI_API_KEY`, or `PUPPYRUN_LLM_PROVIDER=deepseek` with `PUPPYRUN_DEEPSEEK_API_KEY`; keep credential values in private local environment files or shell exports only. Reddit collection is gated by `PUPPYRUN_ENABLE_REDDIT=false` and remains disabled by default.

Phase 0 remains closed at the repository scope: the local deployable skeleton and the temporary VPS public demo loop have both been verified.

## Design

- [PuppyRun design spec](docs/superpowers/specs/2026-05-21-puppyrun-design.md)
- [Phase 0 implementation plan](docs/superpowers/plans/2026-05-21-puppyrun-phase-0-plan.md)
- [Phase 1 implementation plan](docs/superpowers/plans/2026-05-27-puppyrun-phase-1-plan.md)
- [Phase 2 implementation plan](docs/superpowers/plans/2026-06-04-puppyrun-phase-2-plan.md)
- [Phase 3 implementation plan](docs/superpowers/plans/2026-06-08-puppyrun-phase-3-plan.md)
- [Accepted debt](docs/accepted-debt.md)
- [VPS public demo deployment design](docs/superpowers/specs/2026-05-23-puppyrun-public-demo-deployment-design.md)
- [VPS public demo deployment plan](docs/superpowers/plans/2026-05-23-puppyrun-public-demo-deployment-plan.md)

## AI Agent Operating Docs

- [Repository agent instructions](AGENTS.md)
- [Repo-local PuppyRun workflow skill](.agents/skills/puppyrun-agent-workflow/SKILL.md)
- [Repo-local Codex hooks](.codex/hooks.json)
- [Implementation controller prompt](docs/ai-prompts/implementation-controller.md)
- [Read-only reviewer prompt](docs/ai-prompts/read-only-reviewer.md)
- [Narrow unblocker prompt](docs/ai-prompts/narrow-unblocker.md)
- [Task contract snippet](docs/ai-prompts/task-contract.md)

## Naming

- Product name: `PuppyRun`
- Repository name: `puppy-run`
- Python package name: `puppyrun`

## Phase 0 Stack

- Backend API: Python 3.12 + FastAPI
- Database: PostgreSQL
- Queue/cache: Redis
- Worker: arq
- First client: React + TypeScript + Vite
- Local orchestration: Docker Compose

## Local Development

Copy `.env.example` to `.env`, then use Docker Compose:

```bash
cp .env.example .env
docker compose up --build
```

Expected local URLs:

- API health: `http://localhost:8000/health`
- Web console: `http://localhost:5173`

## Phase 0 Verification

Backend tests:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
pytest -q
```

Frontend build:

```bash
cd apps/web
npm install
npm test
npm run build
```

Docker Compose:

```bash
docker compose up --build
```

Smoke test:

```bash
curl http://localhost:8000/health
```

Manual browser acceptance:

1. Open `http://localhost:5173`.
2. Create a session.
3. Answer the clarification prompt.
4. Click `Run Phase 1 Agent`.
5. Without clicking `Refresh`, wait for polling to update the selected session to `completed` and show the recommendation, evidence, and trace.

## Phase 2 Local Smoke

Run backend checks:

```bash
cd backend
. .venv/bin/activate
ruff check .
pytest -q
```

Run frontend checks:

```bash
cd apps/web
npm test -- --run
npm run build
```

Start or refresh the local stack:

```bash
test -f .env || cp .env.example .env
docker compose up --build -d
curl http://localhost:8000/health
docker compose ps
```

Expected health response:

```json
{"status":"ok","service":"puppyrun-api"}
```

Manual Phase 2 browser acceptance at `http://localhost:5173`:

1. Create a session with a prompt comparing LangGraph, OpenAI Agents SDK, CrewAI, and AutoGen for a Python web Agent runtime that needs checkpointing, human approval, and traceable tool calls.
2. Answer the clarification prompt with checkpointing, human approval, Python preference, and observability priority.
3. Click `Run Phase 1 Agent` and wait for `completed`.
4. Confirm the version rail shows `v1`.
5. Use workbench controls to require `checkpointing`.
6. Set `Runtime control and state` weight to `40`.
7. Add custom candidate `AutoGen` with slug `autogen` and repository `microsoft/autogen`.
8. Confirm gap analysis lists changed candidate `autogen`, changed constraint `checkpointing`, changed weight `Runtime control and state`, and one required GitHub fetch.
9. Click `Run targeted re-research` and wait for `completed`.
10. Confirm the version rail shows `v1` and active `v2`.
11. Confirm the recommendation starts with `Recommended v2:`.
12. Confirm the evidence matrix has clickable score cells and a clicked cell opens the evidence drawer.
13. Confirm the ADR view starts with `ADR 0002:`.
14. Confirm trace includes `phase2_started`, `targeted_research_planned`, and `recommendation_version_created`.

## Phase 3 Local Smoke

Deterministic no-key smoke uses the default `.env.example` values and does not make live LLM, Tavily, or Reddit calls:

```bash
test -f .env || cp .env.example .env
docker compose up --build -d
curl http://localhost:8000/health
docker compose ps
```

Expected health response:

```json
{"status":"ok","service":"puppyrun-api"}
```

For optional live DeepSeek smoke, keep credentials outside committed files and run the backend/worker with these private environment variables set:

```bash
PUPPYRUN_LLM_PROVIDER=deepseek
PUPPYRUN_DEEPSEEK_MODEL=deepseek-v4-flash
PUPPYRUN_DEEPSEEK_API_KEY=<private value>
PUPPYRUN_DEEPSEEK_BASE_URL=https://api.deepseek.com
PUPPYRUN_TAVILY_API_KEY=<private value>
PUPPYRUN_ENABLE_REDDIT=false
```

For optional live OpenAI and Tavily smoke, use:

```bash
PUPPYRUN_LLM_PROVIDER=openai
PUPPYRUN_OPENAI_API_KEY=<private value>
PUPPYRUN_TAVILY_API_KEY=<private value>
PUPPYRUN_ENABLE_REDDIT=false
```

Expected Phase 3 browser acceptance at `http://localhost:5173`:

1. Create a session comparing LangGraph, OpenAI Agents SDK, CrewAI, AutoGen, and Dify.
2. Answer clarification with checkpointing, human approval, Python preference, observability priority, and maintenance/community-risk concerns.
3. Click `Run Phase 1 Agent` and wait for `completed`.
4. Confirm the version rail shows `v1`.
5. Confirm the workbench shows the risk panel, claims, verification tasks, tool calls, source filters, adjusted recommendation rationale, and ADR risk output.
6. Confirm skipped provider/tool state is visible when optional sources are disabled or missing credentials.
7. Make a Phase 2 edit that triggers targeted re-research.
8. Click `Run targeted re-research` and wait for `completed`.
9. Confirm `v2` contains versioned Phase 3 rows and `v1` remains readable from the version rail.

Only confirmed risks affect scores, capped conservatively. Contradicted, unresolved, and unverified risks remain visible but do not change score. Reddit remains gated and disabled by default; do not enable it without a deliberate private local setup.

Public URL status:

- `codex/phase-0` verified the local deployable skeleton with Docker Compose.
- The previous Render Blueprint direction has been canceled.
- The current public demo target is direct VPS deployment using Docker Compose and a reverse proxy.
- Temporary raw-IP HTTP public URL verification passed on `2026-05-26`; the real IP is kept in private local notes, not in repo docs.
- Domain DNS and HTTPS setup are external VPS/domain operations, not a remaining repository task.
- Real public hosts are intentionally not committed to this repository. Keep them in the VPS `deploy/vps/.env` file or private deployment notes.

## Public Demo Deployment

Phase 0 public demo deployment targets a VPS. The intended topology is:

- The included VPS path uses Caddy to terminate public HTTP/HTTPS traffic.
- Static React/Vite assets are served from the VPS.
- `/api/*` and `/health` proxy to the private FastAPI API container.
- The API and worker containers share private PostgreSQL and Redis containers.
- Alembic migrations run before API startup.

The application code should stay portable across Docker, environment variables, PostgreSQL, Redis, and Alembic. VPS-specific configuration belongs in deployment files and documentation, not in the Python or React runtime logic.

For VPS deployment, `POSTGRES_PASSWORD` is the raw database password for PostgreSQL, and `POSTGRES_PASSWORD_URLENCODED` is the URL-encoded value used inside `PUPPYRUN_DATABASE_URL` for API and worker containers. URL-encode characters such as `@`, `:`, `/`, `%`, and `#`. Generate the encoded value with:

```sh
python3 - <<'PY'
from urllib.parse import quote
print(quote(input("PostgreSQL password: "), safe=""))
PY
```

Put the command output in `POSTGRES_PASSWORD_URLENCODED` and keep the original raw value in `POSTGRES_PASSWORD`.

Public host handling:

- The checked-in deployment reads the public host from the VPS-local `deploy/vps/.env` file.
- For a temporary raw-IP HTTP demo, configure that file on the VPS and verify the web page plus `/health` through that host.
- For a domain-backed HTTPS demo, point DNS to the VPS, open `80/443`, set the domain in the VPS-local env file, restart the stack, and verify the same smoke test.
- Do not commit real public URLs, VPS IPs, SSH targets, or secrets to repository docs.

Public demo data is disposable. Anyone with the URL can create demo sessions, so do not enter private prompts, secrets, credentials, or confidential project details.

### Public Smoke Test

1. Open the public web URL.
2. Create a decision session.
3. Answer the clarification prompt.
4. Click `Run Phase 1 Agent`.
5. Do not click `Refresh`.
6. Wait until the selected session shows `completed` with recommendation, evidence, and trace output.
7. Open the public API health URL and confirm:

```json
{"status":"ok","service":"puppyrun-api"}
```

Phase 0 public URL verification passes only when the public web page and hosted async worker loop both work through the VPS-hosted PostgreSQL and Redis services.
