# PuppyRun

PuppyRun is an agentic, evidence-grounded workbench for technical stack and architecture decisions.

The first demo workflow focuses on AI Agent technology stack selection. The first client is a web console, while the core decision workflow is designed to be reusable by future clients such as desktop, mobile, CLI, or IDE integrations.

The project is currently in Phase 0: deployable skeleton.

## Design

- [PuppyRun design spec](docs/superpowers/specs/2026-05-21-puppyrun-design.md)
- [Phase 0 implementation plan](docs/superpowers/plans/2026-05-21-puppyrun-phase-0-plan.md)

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
3. Click `Start dummy Agent run`.
4. Without clicking `Refresh`, wait for polling to update both the session list and the `Run status` detail panel to `completed`.

Public URL status:

- `codex/phase-0` currently verifies the local deployable skeleton with Docker Compose.
- No public deployment target or public URL is configured in this branch yet.
- Public hosting should be handled by a follow-up deployment plan after choosing the target platform for the API, worker, PostgreSQL, Redis, and web client.
