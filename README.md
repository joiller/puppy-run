# PuppyRun

PuppyRun is an agentic, evidence-grounded workbench for technical stack and architecture decisions.

The first demo workflow focuses on AI Agent technology stack selection. The first client is a web console, while the core decision workflow is designed to be reusable by future clients such as desktop, mobile, CLI, or IDE integrations.

The project is currently in Phase 0: deployable skeleton.

## Design

- [PuppyRun design spec](docs/superpowers/specs/2026-05-21-puppyrun-design.md)
- [Phase 0 implementation plan](docs/superpowers/plans/2026-05-21-puppyrun-phase-0-plan.md)
- [VPS public demo deployment design](docs/superpowers/specs/2026-05-23-puppyrun-public-demo-deployment-design.md)
- [VPS public demo deployment plan](docs/superpowers/plans/2026-05-23-puppyrun-public-demo-deployment-plan.md)

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

- `codex/phase-0` verified the local deployable skeleton with Docker Compose.
- The previous Render Blueprint direction has been canceled.
- The current public demo target is direct VPS deployment using Docker Compose and a reverse proxy.
- Temporary raw-IP HTTP public URL verification passed on `2026-05-26`; the real IP is kept in private local notes, not in repo docs.
- A domain-backed HTTPS URL is still pending.

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

Expected public URLs after deployment:

- Temporary raw-IP web: `http://<vps-ip>`
- Temporary raw-IP API health: `http://<vps-ip>/health`
- Domain HTTPS web: `https://<public-demo-host>` when DNS is configured.
- Domain HTTPS API health: `https://<public-demo-host>/health` when DNS is configured.

Public demo data is disposable. Anyone with the URL can create demo sessions, so do not enter private prompts, secrets, credentials, or confidential project details.

### Public Smoke Test

1. Open the public web URL.
2. Create a decision session.
3. Click `Start dummy Agent run`.
4. Do not click `Refresh`.
5. Wait until the selected session detail panel shows `completed`.
6. Open the public API health URL and confirm:

```json
{"status":"ok","service":"puppyrun-api"}
```

Phase 0 public URL verification passes only when the public web page and hosted async worker loop both work through the VPS-hosted PostgreSQL and Redis services.
