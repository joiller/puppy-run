# PuppyRun VPS Public Demo Deployment Design

Date: 2026-05-25

## 1. Goal

PuppyRun Phase 0 has a working local deployable skeleton. The remaining Phase 0 gap is public URL verification. This design defines the smallest VPS deployment that proves the existing Phase 0 architecture online without starting Phase 1 Agent behavior.

The public demo must prove the same runtime loop that already works locally:

```text
Public web console
  -> public reverse proxy
  -> private FastAPI API
  -> PostgreSQL decision session row
  -> Redis/arq queued job
  -> private background worker status update
  -> public web console polling update
```

The success criterion is not just that a static page loads. A reviewer must be able to open the public web URL, create a session, start the dummy Agent run, and see the status update to `completed`.

## 2. Scope

### In Scope

- Deploy the React/Vite web console to a public HTTPS URL on a VPS.
- Deploy the FastAPI API as a private container on the same VPS.
- Deploy the arq worker as a private background container on the same VPS.
- Run PostgreSQL for decision sessions, agent runs, and events on the VPS.
- Run Redis for arq queue storage on the VPS.
- Run Alembic migrations during API startup.
- Configure production-like environment variables for API, worker, and web build.
- Configure the reverse proxy so the public web console can call the API through same-origin paths.
- Document public demo URLs and smoke-test steps in `README.md`.
- Preserve the local Docker Compose path for development.
- Preserve application-level portability for later Kubernetes deployment.

### Out Of Scope

- Render, Railway, Fly.io, Cloud Run, Vercel, Netlify, or another managed PaaS deployment.
- Real LLM calls.
- Real candidate discovery, criteria generation, evidence collection, or scoring.
- User accounts, organization workspaces, or full authentication.
- Rate limiting, quota enforcement, cost budget enforcement, or admin UI.
- Eval dashboard, MCP adapters, evidence matrix, ADR view, or decision versioning.
- Production-grade monitoring, alerting, backups, and incident response.
- Kubernetes manifests, Helm charts, or GitOps configuration.

Those items belong to later phases. This deployment is a Phase 0 public demo gate, not Phase 1 product logic or Phase 5 hardening.

## 3. Platform Choice

Use a self-managed VPS as the Phase 0 public demo target.

The previous Render Blueprint direction is canceled. The repository should not keep a root-level `render.yaml`, and the implementation plan should not instruct future workers to create Render resources.

Reasons:

- VPS deployment matches the user's intended near-term hosting direction more directly than a temporary PaaS adapter.
- Docker Compose can run the same conceptual units already proven locally: web, API, worker, PostgreSQL, and Redis.
- A reverse proxy on the VPS keeps the public surface simple: one public host, same-origin frontend API calls, private backend services.
- The deployment remains easy to explain in interviews because it demonstrates the actual runtime topology instead of relying on provider-specific dashboards.
- The same process contracts remain useful for later Kubernetes work.

Tradeoffs:

- The VPS owner is responsible for OS patching, firewall rules, SSH access, Docker installation, TLS termination, and resource monitoring.
- PostgreSQL and Redis are no longer managed services. Phase 0 can accept this if the README clearly states that demo data is disposable.
- Public verification must include the worker loop, because successful reverse proxy and API health checks alone do not prove the async decision flow.

## 4. Portability Requirements

The Phase 0 public demo should make later Kubernetes deployment easier, not harder.

The stable deployment contract is:

- `apps/web` builds to static assets with `npm run build`.
- The public web build can call the API through a same-origin path.
- `backend/Dockerfile` builds a backend image that can run either API or worker commands.
- API startup is a normal long-running process that binds to `0.0.0.0`.
- Worker startup is a normal long-running process that runs `arq puppyrun_worker.main.WorkerSettings`.
- PostgreSQL is reached through `PUPPYRUN_DATABASE_URL`.
- Redis is reached through `PUPPYRUN_REDIS_URL`.
- Alembic owns schema migration.
- CORS is configured through `PUPPYRUN_CORS_ORIGINS`.

The implementation must avoid:

- calling VPS-provider APIs from application code
- relying on host-only absolute filesystem paths inside application code
- exposing PostgreSQL or Redis to the public internet
- hard-coding one public domain in Python or React source files
- replacing Docker Compose local development with a VPS-only workflow
- using the Vite development server as the public web server

For the later Kubernetes target, these same contracts should map to Deployments for API and worker, a static web container or CDN-backed static hosting, Secrets/ConfigMaps for env, Services/Ingress for networking, and managed or in-cluster PostgreSQL/Redis depending on the production decision at that time.

## 5. Deployment Topology

### 5.1 Public Reverse Proxy And Static Web

Use Caddy by default because it can serve static assets and manage HTTPS certificates with minimal configuration. Nginx remains an acceptable substitute if the VPS already standardizes on it.

Public responsibilities:

- listen on ports `80` and `443`
- serve the React/Vite static build
- proxy `/api/*` to the private API container
- proxy `/health` to the private API container
- keep PostgreSQL, Redis, API, and worker ports off the public internet

The web build should prefer same-origin API calls. For the current frontend, this means building with an empty `VITE_API_BASE_URL`, so browser requests such as `/api/v1/sessions` go through the reverse proxy.

### 5.2 API Service

Runtime shape: Docker, using the existing `backend/Dockerfile`.

The API startup must:

- install backend dependencies from `backend/pyproject.toml`
- run Alembic migrations before the server starts
- start `uvicorn puppyrun_api.main:app`
- bind on `0.0.0.0`
- expose `/health` through the reverse proxy

Required environment:

```text
PUPPYRUN_ENV=production
PUPPYRUN_DATABASE_URL=postgresql+asyncpg://puppyrun:${POSTGRES_PASSWORD_URLENCODED}@postgres:5432/puppyrun
PUPPYRUN_REDIS_URL=redis://redis:6379/0
PUPPYRUN_CORS_ORIGINS=["https://<public-demo-host>"]
```

In VPS env files, `POSTGRES_PASSWORD` is the raw database password passed to the PostgreSQL container. `POSTGRES_PASSWORD_URLENCODED` is the URL-encoded form used inside `PUPPYRUN_DATABASE_URL` for API and worker containers; encode URL-significant characters such as `@`, `:`, `/`, `%`, and `#`.

The API should remain stateless except for PostgreSQL and Redis.

### 5.3 Background Worker

Start command:

```bash
arq puppyrun_worker.main.WorkerSettings
```

Required environment:

```text
PUPPYRUN_ENV=production
PUPPYRUN_DATABASE_URL=<same database URL as API>
PUPPYRUN_REDIS_URL=<same redis URL as API>
```

The worker has no public URL. It consumes jobs from Redis and writes status changes to PostgreSQL.

### 5.4 PostgreSQL

Phase 0 requirements:

- one database is enough
- schema is managed by Alembic
- storage should use a named Docker volume
- no production backup policy is required for Phase 0, but the README should state that public demo data is disposable

### 5.5 Redis

Phase 0 requirements:

- Redis is reachable only on the private Docker network
- queue state can use a named Docker volume
- no direct public access

The worker queue is still only used for the dummy Agent job in Phase 0.

## 6. Configuration Design

### 6.1 Remove Render Configuration

The root-level `render.yaml` should be removed and should not be recreated for this VPS plan.

Current docs should refer to VPS deployment, not managed-PaaS service topology or old Render public URL patterns.

### 6.2 VPS Deployment Files

The follow-up implementation should add a focused `deploy/vps/` directory:

```text
deploy/vps/
  docker-compose.yml
  web.Dockerfile
  Caddyfile
  .env.example
  README.md
```

Responsibilities:

- `docker-compose.yml`: defines web/reverse-proxy, API, worker, PostgreSQL, Redis, private networks, volumes, restart policy, and health checks.
- `web.Dockerfile`: builds `apps/web` and packages static assets into the reverse-proxy image.
- `Caddyfile`: serves static assets and proxies API routes.
- `.env.example`: documents required VPS values without committing secrets.
- `README.md`: explains first deployment, update, restart, log, and smoke-test commands.

### 6.3 Backend Port And URL Handling

The backend already supports portable PostgreSQL URL normalization and platform `PORT` fallback. Keep that compatibility because it remains useful on a VPS and for later Kubernetes work.

For the VPS compose file, the API can bind to a fixed private container port such as `8000`; the reverse proxy owns public ports `80` and `443`.

### 6.4 CORS

Production CORS should allow only the public web origin.

Local development should keep allowing:

```text
http://localhost:5173
```

If the frontend uses same-origin API paths through the reverse proxy, CORS is mostly a defense-in-depth setting rather than the primary browser path. It should still be configured correctly because direct API testing through the public host is expected.

### 6.5 Public Demo Data Boundary

Phase 0 does not have user accounts. Public demo data should be treated as disposable.

The README should state:

- anyone with the URL can create demo sessions
- no secrets or private prompts should be entered
- data may be reset without notice

If abuse becomes a concern, add a later demo gate such as basic auth, a shared demo passphrase, or session quota. Do not include that in this Phase 0 deployment unless public sharing risk makes it necessary.

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

### 8.2 VPS Configuration Verification

Before touching a remote VPS, validate the deployment files locally:

```bash
docker compose --env-file deploy/vps/.env.example -f deploy/vps/docker-compose.yml config
docker compose --env-file deploy/vps/.env.example -f deploy/vps/docker-compose.yml build
```

Expected: Docker Compose resolves configuration and builds the API and web images.

### 8.3 Public VPS Verification

After the VPS deployment is running:

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

The public deployment passes Phase 0 only when both the web page and the async worker loop work through the VPS-hosted PostgreSQL and Redis services.

## 9. Documentation Updates

Update `README.md` with:

- public web URL placeholder or final URL after deployment
- public API health URL placeholder or final URL after deployment
- VPS topology
- required environment variables
- local development command
- public smoke-test steps
- warning that public demo data is disposable
- portability note for later Kubernetes deployment

If final public URLs are not known before merging the deployment configuration, document the host variable and replace it after the first successful deployment.

## 10. Risks And Mitigations

- **VPS exposure risk:** only ports `80`, `443`, and SSH should be public; PostgreSQL, Redis, API, and worker stay on the Docker network.
- **TLS or DNS failure:** use Caddy automatic HTTPS when a domain points to the VPS; validate DNS before public smoke testing.
- **Host resource pressure:** Phase 0 can run all services on one small VPS, but logs and memory should be checked after the first run.
- **Migration/startup failure:** API startup command must run Alembic before `uvicorn`, and failures should stop deployment.
- **Worker not running:** public verification must include dummy run completion, not just API health.
- **Public write access:** acceptable for Phase 0 only with disposable demo data; add auth or quota later if sharing more widely.
- **Manual server drift:** keep VPS topology in Git under `deploy/vps/`; avoid undocumented dashboard or shell-only configuration.
- **Future Kubernetes drift:** preserve Docker, env var, PostgreSQL, Redis, and Alembic contracts so later deployment work is configuration-heavy rather than application rewrite-heavy.

## 11. Implementation Boundary

The follow-up implementation plan should be small and sequential:

1. Remove Render-specific deployment artifacts.
2. Add VPS deployment configuration under `deploy/vps/`.
3. Verify existing backend URL/port compatibility still passes.
4. Update documentation.
5. Verify locally.
6. Deploy to the VPS only after user authorization.
7. Run public smoke tests.

Do not combine this work with Phase 1 Agent runtime features. The only Agent behavior in this deployment is the existing dummy Agent job.

Do not add Kubernetes manifests in this Phase 0 public demo task. Kubernetes remains a later deployment track.
