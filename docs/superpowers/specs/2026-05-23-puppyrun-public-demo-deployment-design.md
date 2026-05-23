# PuppyRun Public Demo Deployment Design

Date: 2026-05-23

## 1. Goal

PuppyRun Phase 0 has a working local deployable skeleton. The remaining Phase 0 gap is public URL verification. This design defines the smallest public demo deployment that proves the existing Phase 0 architecture online without starting Phase 1 Agent behavior.

The public demo must prove the same runtime loop that already works locally:

```text
Public web console
  -> public FastAPI API
  -> PostgreSQL decision session row
  -> Redis/arq queued job
  -> background worker status update
  -> public web console polling update
```

The success criterion is not just that a static page loads. A reviewer must be able to open the public web URL, create a session, start the dummy Agent run, and see the status update to `completed`.

## 2. Scope

### In Scope

- Deploy the React/Vite web console to a public URL.
- Deploy the FastAPI API to a public URL.
- Deploy the arq worker as a private background process.
- Provision hosted PostgreSQL for decision sessions, agent runs, and events.
- Provision hosted Redis-compatible queue storage for arq.
- Run Alembic migrations during API deployment startup.
- Configure production-like environment variables for API, worker, and web build.
- Configure CORS so the public web console can call the public API.
- Document public demo URLs and smoke-test steps in `README.md`.
- Preserve the local Docker Compose path for development.
- Preserve application-level portability for the later VPS and Kubernetes deployment target.

### Out Of Scope

- Real LLM calls.
- Real candidate discovery, criteria generation, evidence collection, or scoring.
- User accounts, organization workspaces, or full authentication.
- Rate limiting, quota enforcement, cost budget enforcement, or admin UI.
- Eval dashboard, MCP adapters, evidence matrix, ADR view, or decision versioning.
- Production-grade monitoring, alerting, backups, and incident response.
- VPS provisioning, reverse proxy setup, Kubernetes manifests, Helm charts, or GitOps configuration.

Those items belong to later phases. This deployment is a Phase 0 public demo gate, not Phase 1 product logic or Phase 5 hardening.

## 3. Platform Choice

Use Render Blueprint as the first public demo target.

Render is only the Phase 0 public demo adapter. The final production-oriented target for PuppyRun is VPS and Kubernetes deployment. This means the implementation must not put PuppyRun runtime assumptions inside Render-specific behavior. Render-specific configuration belongs in `render.yaml`; application code should continue to depend on portable process contracts, environment variables, PostgreSQL, Redis, Docker images, and Alembic migrations.

Reasons:

- Render supports web services, static sites, background workers, PostgreSQL, Redis-compatible Key Value, environment variables, health checks, and private service communication.
- The current PuppyRun shape already maps cleanly to separate deployable units: web, API, worker, PostgreSQL, and Redis.
- A `render.yaml` Blueprint keeps the deployment topology in Git instead of relying only on dashboard state.
- The deployment structure remains easy to explain in interviews and supports the next Phase 1 thin slice without changing the overall architecture.

Railway remains a reasonable fallback because it also supports multi-service projects, managed databases, Redis, Dockerfile builds, public networking, and private networking. Fly.io is more powerful but should be deferred because it introduces more infrastructure responsibility than Phase 0 needs.

## 4. Portability Requirements

The Phase 0 public demo should make later VPS and Kubernetes deployment easier, not harder.

The stable deployment contract is:

- `apps/web` builds to static assets with `npm run build`.
- `backend/Dockerfile` builds a backend image that can run either API or worker commands.
- API startup is a normal long-running process that binds to `0.0.0.0` and reads `PORT`.
- Worker startup is a normal long-running process that runs `arq puppyrun_worker.main.WorkerSettings`.
- PostgreSQL is reached through `PUPPYRUN_DATABASE_URL`.
- Redis is reached through `PUPPYRUN_REDIS_URL`.
- Alembic owns schema migration.
- CORS is configured through `PUPPYRUN_CORS_ORIGINS`.

The implementation must avoid:

- calling Render APIs from application code
- relying on Render-only filesystem paths
- hard-coding `onrender.com` domains outside deployment configuration and documentation
- assuming managed database connection string formats without normalization
- replacing Docker Compose local development with a Render-only workflow

For the later VPS target, these same contracts should map to Docker Compose plus a reverse proxy such as Caddy or Nginx. For the later Kubernetes target, they should map to Deployments for API and worker, a static web container or CDN-backed static hosting, Secrets/ConfigMaps for env, Services/Ingress for networking, and managed or in-cluster PostgreSQL/Redis depending on the production decision at that time.

## 5. Deployment Topology

### 5.1 Web Static Site

Service type: Render Static Site.

Source directory:

```text
apps/web
```

Build command:

```bash
npm install && npm run build
```

Publish directory:

```text
dist
```

Build-time environment:

```text
VITE_API_BASE_URL=https://<puppyrun-api>.onrender.com
```

The public web URL is the primary Phase 0 public URL. The browser calls the public API URL directly. No secrets are embedded in the frontend.

### 5.2 API Web Service

Service type: Render Web Service.

Runtime shape: Docker, using the existing `backend/Dockerfile` as the deploy image for both API and worker services. The API service overrides the container command for hosted startup.

The API startup must:

- install backend dependencies from `backend/pyproject.toml`
- run Alembic migrations before the server starts
- start `uvicorn puppyrun_api.main:app`
- bind on `0.0.0.0`
- read the listening port from the platform-provided `PORT` environment variable, with local fallback to `8000`
- expose `/health` as the health check path

Required environment:

```text
PUPPYRUN_ENV=production
PUPPYRUN_DATABASE_URL=<hosted postgres URL converted for asyncpg if needed>
PUPPYRUN_REDIS_URL=<hosted redis/key-value URL>
PUPPYRUN_CORS_ORIGINS=["https://<puppyrun-web>.onrender.com"]
```

The API should remain stateless except for PostgreSQL and Redis.

### 5.3 Background Worker

Service type: Render Background Worker.

Start command:

```bash
arq puppyrun_worker.main.WorkerSettings
```

Required environment:

```text
PUPPYRUN_ENV=production
PUPPYRUN_DATABASE_URL=<same database URL as API>
PUPPYRUN_REDIS_URL=<same redis/key-value URL as API>
```

The worker has no public URL. It consumes jobs from Redis and writes status changes to PostgreSQL.

### 5.4 PostgreSQL

Service type: Render PostgreSQL.

Phase 0 requirements:

- one database is enough
- schema is managed by Alembic
- no production backup policy is required for Phase 0, but the README should state that public demo data is disposable

The implementation should make backend configuration tolerant of provider URL formats. Hosted platforms often provide `postgres://` or `postgresql://` connection strings; SQLAlchemy async code needs `postgresql+asyncpg://`.

### 5.5 Redis-Compatible Queue

Service type: Render Key Value, used as the Redis-compatible queue backend for arq.

Phase 0 requirements:

- private/internal connection string for API and worker
- enough persistence for arq queue processing
- no direct public access

The worker queue is still only used for the dummy Agent job in Phase 0.

## 6. Configuration Design

### 6.1 `render.yaml`

Add a root-level `render.yaml` that declares:

- web static site
- API web service
- worker background service
- PostgreSQL database
- Redis-compatible queue service
- environment variable wiring between services
- health check path for the API

Secrets and generated connection strings should not be committed. Values that Render generates should be referenced through Blueprint environment variable bindings.

### 6.2 Backend Port Handling

The backend must support both local and hosted startup.

Expected behavior:

- local default remains port `8000`
- hosted service uses `PORT` if present
- Docker Compose continues to work without setting `PORT`

This should be handled either through the Render start command or through backend settings. The implementation should prefer the smallest change that keeps local development unchanged.

### 6.3 Database URL Normalization

Backend settings should normalize PostgreSQL URLs before SQLAlchemy engine creation:

- `postgres://...` -> `postgresql+asyncpg://...`
- `postgresql://...` -> `postgresql+asyncpg://...`
- `postgresql+asyncpg://...` -> unchanged

This avoids provider-specific connection string failures while preserving the existing local `.env.example`.

### 6.4 CORS

Production CORS should allow only the public web URL.

Local development should keep allowing:

```text
http://localhost:5173
```

The implementation must avoid `*` CORS in the public demo because the API can mutate demo state.

### 6.5 Public Demo Data Boundary

Phase 0 does not have user accounts. Public demo data should be treated as disposable.

The README should state:

- anyone with the URL can create demo sessions
- no secrets or private prompts should be entered
- data may be reset without notice

If abuse becomes a concern, add a later demo gate such as basic auth, a shared demo passphrase, or session quota. Do not include that in this Phase 0 deployment unless required by the hosting platform or immediate public sharing risk.

## 7. Local Development Compatibility

The deployment work must not break the current local contract:

```bash
cp .env.example .env
docker compose up --build
```

Expected local URLs remain:

```text
http://localhost:8000/health
http://localhost:5173
```

Backend tests, web tests, and the Docker Compose smoke path should continue to pass after deployment configuration changes.

## 8. Verification

### 8.1 Local Verification

Run:

```bash
cd backend
. .venv/bin/activate
ruff check .
pytest -q
```

Run:

```bash
cd apps/web
npm test
npm run build
```

Run Docker Compose:

```bash
docker compose up --build -d
curl http://localhost:8000/health
```

Then manually verify the local web console still creates a session, starts a dummy Agent run, and updates the selected session detail panel to `completed`.

### 8.2 Public Verification

After Render deploys:

1. Open the public web URL.
2. Confirm the app loads without browser console API base URL errors.
3. Create a decision session.
4. Start the dummy Agent run.
5. Wait without manual refresh.
6. Confirm the selected session detail panel updates to `completed`.
7. Open the public API health URL and confirm it returns:

```json
{"status":"ok","service":"puppyrun-api"}
```

The public deployment passes Phase 0 only when both the web page and the async worker loop work through hosted PostgreSQL and Redis.

## 9. Documentation Updates

Update `README.md` with:

- public web URL placeholder or final URL after deployment
- public API health URL placeholder or final URL after deployment
- Render service topology and the note that Render is a Phase 0 demo adapter, not the final production target
- required environment variables
- local development command
- public smoke-test steps
- warning that public demo data is disposable
- portability note for later VPS and Kubernetes deployment

If final public URLs are not known before merging the deployment configuration, document placeholders and replace them after the first successful deployment.

## 10. Risks And Mitigations

- **Provider URL mismatch:** normalize PostgreSQL URLs before creating the async SQLAlchemy engine.
- **Vite env mismatch:** `VITE_API_BASE_URL` must be present at web build time, not only runtime.
- **CORS failure:** production API must include the exact public web origin.
- **Migration/startup failure:** API startup command must run Alembic before `uvicorn`, and failures should stop deployment.
- **Worker not running:** public verification must include dummy run completion, not just API health.
- **Free-tier sleeping or cold starts:** acceptable for Phase 0 demo if documented; not acceptable for later production-hardening phases.
- **Public write access:** acceptable for Phase 0 only with disposable demo data; add auth or quota later if sharing more widely.
- **Platform lock-in:** keep Render-specific logic in `render.yaml` and documentation, not in application code.
- **Future VPS/Kubernetes drift:** preserve Docker, env var, PostgreSQL, Redis, and Alembic contracts so later deployment work is configuration-heavy rather than application rewrite-heavy.

## 11. Implementation Boundary

The follow-up implementation plan should be small and sequential:

1. Add deployment configuration.
2. Add backend URL/port compatibility.
3. Update web build configuration only as needed.
4. Update documentation.
5. Verify locally.
6. Deploy and run public smoke tests.

Do not combine this work with Phase 1 Agent runtime features. The only Agent behavior in this deployment is the existing dummy Agent job.

Do not add VPS provisioning scripts or Kubernetes manifests in this Phase 0 public demo task. Those are later deployment tracks. This task should only keep the application portable enough that those tracks are straightforward.
