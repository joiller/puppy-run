# PuppyRun VPS Public Demo Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a VPS-based public demo deployment path that proves the Phase 0 Web -> API -> PostgreSQL -> Redis/arq worker -> Web polling loop online without using Render.

**Architecture:** Keep application runtime portable through Docker images, environment variables, PostgreSQL, Redis, Alembic migrations, a standard FastAPI process, and a standard arq worker process. Put VPS-specific behavior under `deploy/vps/`: Docker Compose for process topology and Caddy for static web serving, HTTPS, and API reverse proxying. Do not add real Agent logic or Kubernetes manifests in this plan.

**Tech Stack:** VPS, Docker Compose, Caddy, Docker, FastAPI, SQLAlchemy async, Alembic, PostgreSQL, Redis, arq, React/Vite static build, pytest, ruff, npm.

---

## Current Baseline

This branch already contains portable backend deployment compatibility from the earlier public-demo work:

- `backend/tests/test_config.py` covers PostgreSQL URL normalization and platform `PORT` handling.
- `backend/puppyrun_api/config.py` exposes `normalize_database_url()` and `Settings.sqlalchemy_database_url`.
- `backend/puppyrun_api/db.py` uses the normalized async SQLAlchemy URL.
- `backend/Dockerfile` installs production dependencies and can run API or worker commands.

Keep those changes. They are provider-neutral and still useful for VPS and later Kubernetes deployment.

---

## File Structure

Create and modify these files:

- Removed: `render.yaml`
  - Render is no longer the deployment target; future implementation should confirm it is absent and should not recreate it.
- Create: `deploy/vps/docker-compose.yml`
  - Defines the VPS web/reverse-proxy, API, worker, PostgreSQL, Redis, volumes, and private networking.
- Create: `deploy/vps/web.Dockerfile`
  - Builds the React/Vite static bundle and packages it with Caddy.
- Create: `deploy/vps/Caddyfile`
  - Serves the static web app and proxies `/api/*` and `/health` to the API container.
- Create: `deploy/vps/.env.example`
  - Documents required VPS environment values without committing secrets.
- Create: `deploy/vps/README.md`
  - Documents VPS setup, deployment, update, logs, and smoke-test commands.
- Modify: `README.md`
  - Documents the VPS public demo target, smoke tests, portability boundary, and disposable demo data warning.

Do not modify Phase 1 Agent workflow code in this plan.

---

### Task 1: Confirm Render Deployment Artifact Is Absent

**Files:**
- No new files.

- [ ] **Step 1: Confirm no root Render config remains**

Run:

```bash
test ! -f render.yaml
```

Expected: command exits successfully.

- [ ] **Step 2: Confirm Git no longer tracks a live Render config file**

```bash
git ls-files render.yaml
```

Expected: no output after the VPS documentation switch has been committed.

---

### Task 2: VPS Deployment File Skeleton

**Files:**
- Create: `deploy/vps/docker-compose.yml`
- Create: `deploy/vps/web.Dockerfile`
- Create: `deploy/vps/Caddyfile`
- Create: `deploy/vps/.env.example`

- [ ] **Step 1: Add VPS Docker Compose topology**

Create `deploy/vps/docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: puppyrun
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in deploy/vps/.env}
      POSTGRES_DB: puppyrun
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U puppyrun -d puppyrun"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7
    restart: unless-stopped
    command: redis-server --appendonly yes
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  api:
    build:
      context: ../../backend
    restart: unless-stopped
    environment:
      PUPPYRUN_ENV: production
      PUPPYRUN_DATABASE_URL: postgresql+asyncpg://puppyrun:${POSTGRES_PASSWORD_URLENCODED:?set POSTGRES_PASSWORD_URLENCODED in deploy/vps/.env}@postgres:5432/puppyrun
      PUPPYRUN_REDIS_URL: redis://redis:6379/0
      PUPPYRUN_CORS_ORIGINS: ${PUPPYRUN_CORS_ORIGINS:?set PUPPYRUN_CORS_ORIGINS in deploy/vps/.env}
    command: >
      sh -c "alembic upgrade head &&
             uvicorn puppyrun_api.main:app --host 0.0.0.0 --port 8000"
    expose:
      - "8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker:
    build:
      context: ../../backend
    restart: unless-stopped
    environment:
      PUPPYRUN_ENV: production
      PUPPYRUN_DATABASE_URL: postgresql+asyncpg://puppyrun:${POSTGRES_PASSWORD_URLENCODED:?set POSTGRES_PASSWORD_URLENCODED in deploy/vps/.env}@postgres:5432/puppyrun
      PUPPYRUN_REDIS_URL: redis://redis:6379/0
    command: arq puppyrun_worker.main.WorkerSettings
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  web:
    build:
      context: ../..
      dockerfile: deploy/vps/web.Dockerfile
      args:
        VITE_API_BASE_URL: ""
    restart: unless-stopped
    environment:
      PUPPYRUN_PUBLIC_HOST: ${PUPPYRUN_PUBLIC_HOST:?set PUPPYRUN_PUBLIC_HOST in deploy/vps/.env}
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - caddy-data:/data
      - caddy-config:/config
    depends_on:
      - api

volumes:
  postgres-data:
  redis-data:
  caddy-data:
  caddy-config:
```

- [ ] **Step 2: Add the production web image**

Create `deploy/vps/web.Dockerfile`:

```dockerfile
FROM node:22-slim AS build

WORKDIR /app

COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web/ ./

ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

FROM caddy:2-alpine

COPY deploy/vps/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv/puppyrun
```

- [ ] **Step 3: Add the Caddy reverse proxy configuration**

Create `deploy/vps/Caddyfile`:

```caddyfile
{
	admin off
}

{$PUPPYRUN_PUBLIC_HOST} {
	encode zstd gzip
	root * /srv/puppyrun

	handle /health {
		reverse_proxy api:8000
	}

	handle /api/* {
		reverse_proxy api:8000
	}

	handle {
		try_files {path} /index.html
		file_server
	}
}
```

- [ ] **Step 4: Add the VPS environment example**

Create `deploy/vps/.env.example`:

```dotenv
PUPPYRUN_PUBLIC_HOST=demo.example.com
POSTGRES_PASSWORD=change-this-postgres-password
POSTGRES_PASSWORD_URLENCODED=change-this-postgres-password
PUPPYRUN_CORS_ORIGINS=["https://demo.example.com"]
```

`POSTGRES_PASSWORD` is the raw database password for PostgreSQL. `POSTGRES_PASSWORD_URLENCODED` is the URL-encoded form used inside `PUPPYRUN_DATABASE_URL` for API and worker containers. Generate the encoded value with:

```bash
python3 - <<'PY'
from urllib.parse import quote
print(quote(input("PostgreSQL password: "), safe=""))
PY
```

Put the command output in `POSTGRES_PASSWORD_URLENCODED` and keep the original raw value in `POSTGRES_PASSWORD`.

- [ ] **Step 5: Validate Docker Compose config resolution**

Run:

```bash
docker compose --env-file deploy/vps/.env.example -f deploy/vps/docker-compose.yml config
```

Expected: Docker Compose prints the resolved service configuration without errors.

- [ ] **Step 6: Commit VPS deployment skeleton**

```bash
git add deploy/vps/docker-compose.yml deploy/vps/web.Dockerfile deploy/vps/Caddyfile deploy/vps/.env.example
git commit -m "chore: add vps deployment topology"
```

---

### Task 3: VPS Deployment Runbook

**Files:**
- Create: `deploy/vps/README.md`

- [ ] **Step 1: Add the VPS runbook**

Create `deploy/vps/README.md`:

````markdown
# PuppyRun VPS Deployment

This directory runs the Phase 0 public demo on one VPS with Docker Compose.

## Prerequisites

- A VPS with Docker Engine and Docker Compose installed.
- A DNS record for the public demo host pointing to the VPS.
- Public inbound access to ports `80` and `443`.
- SSH access for deployment.

Only SSH, HTTP, and HTTPS should be exposed publicly. PostgreSQL, Redis, API, and worker services stay on the private Docker network.

## First Deployment

From the repository root on the VPS:

```bash
cp deploy/vps/.env.example deploy/vps/.env
```

Edit `deploy/vps/.env`:

```dotenv
PUPPYRUN_PUBLIC_HOST=demo.example.com
POSTGRES_PASSWORD=change-this-postgres-password
POSTGRES_PASSWORD_URLENCODED=change-this-postgres-password
PUPPYRUN_CORS_ORIGINS=["https://demo.example.com"]
```

Use the real public host and a strong database password before starting services. Keep `POSTGRES_PASSWORD` as the raw value and put its URL-encoded form in `POSTGRES_PASSWORD_URLENCODED`. Generate the encoded value with:

```bash
python3 - <<'PY'
from urllib.parse import quote
print(quote(input("PostgreSQL password: "), safe=""))
PY
```

Put the command output in `POSTGRES_PASSWORD_URLENCODED` and keep the original raw value in `POSTGRES_PASSWORD`.

Start the stack:

```bash
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml up --build -d
```

Check API health through the public reverse proxy. Replace `demo.example.com` with your configured `PUPPYRUN_PUBLIC_HOST`, or export the variable from `deploy/vps/.env` and use it:

```bash
curl "https://${PUPPYRUN_PUBLIC_HOST}/health"
```

Expected:

```json
{"status":"ok","service":"puppyrun-api"}
```

## Update Deployment

After pulling new code on the VPS:

```bash
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml up --build -d
```

## Logs

```bash
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml logs -f api
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml logs -f worker
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml logs -f web
```

## Stop

```bash
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml down
```

Do not remove volumes unless demo data should be reset.

## Public Smoke Test

Use your configured `PUPPYRUN_PUBLIC_HOST` for the public smoke test.

1. Open `https://${PUPPYRUN_PUBLIC_HOST}`.
2. Create a decision session.
3. Click `Start dummy Agent run`.
4. Do not click `Refresh`.
5. Wait until the selected session detail panel shows `completed`.
6. Open `https://${PUPPYRUN_PUBLIC_HOST}/health` and confirm the API health JSON.

Phase 0 public URL verification passes only when both the web page and the async worker loop work through the VPS-hosted PostgreSQL and Redis services.
````

- [ ] **Step 2: Commit the runbook**

```bash
git add deploy/vps/README.md
git commit -m "docs: add vps deployment runbook"
```

---

### Task 4: Existing Backend Compatibility Verification

**Files:**
- No new files.

- [ ] **Step 1: Run backend lint and tests**

Run:

```bash
cd backend
. .venv/bin/activate
ruff check .
pytest -q
```

Expected: PASS.

- [ ] **Step 2: Build the backend image from the VPS compose context**

Run:

```bash
docker compose --env-file deploy/vps/.env.example -f deploy/vps/docker-compose.yml build api worker
```

Expected: both backend service images build successfully.

---

### Task 5: Frontend Build And Same-Origin API Verification

**Files:**
- No new files.

- [ ] **Step 1: Run frontend tests and production build**

Run:

```bash
cd apps/web
npm test
npm run build
```

Expected: PASS.

- [ ] **Step 2: Build the Caddy-backed web image**

Run:

```bash
docker compose --env-file deploy/vps/.env.example -f deploy/vps/docker-compose.yml build web
```

Expected: the web image builds successfully and includes the static Vite output.

---

### Task 6: README Public Deployment Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update public demo deployment docs**

In `README.md`, ensure the public demo section says:

````markdown
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

- Web: `https://<public-demo-host>`
- API health: `https://<public-demo-host>/health`

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
````

- [ ] **Step 2: Confirm current deployment docs do not contain active Render deployment instructions**

Run:

```bash
python3 - <<'PY'
from pathlib import Path

needles = [
    "onrender" + ".com",
    "puppyrun-phase0-" + "web",
    "puppyrun-phase0-" + "api",
    "puppyrun-phase0-" + "worker",
    "puppyrun-phase0-" + "db",
    "puppyrun-phase0-" + "queue",
    "Create the " + "Render",
    "Render " + "dashboard",
    "starter " + "plan",
]
files = [
    Path("README.md"),
    Path("docs/superpowers/specs/2026-05-23-puppyrun-public-demo-deployment-design.md"),
    Path("docs/superpowers/plans/2026-05-23-puppyrun-public-demo-deployment-plan.md"),
]
matches = []
for file_path in files:
    for line_number, line in enumerate(file_path.read_text().splitlines(), start=1):
        if any(needle in line for needle in needles):
            matches.append(f"{file_path}:{line_number}:{line}")
if matches:
    raise SystemExit("\n".join(matches))
PY
```

Expected: no matches. Mentions that the previous Render direction was canceled are acceptable, but there must be no old service names, old Render public URL, or instruction to create Render resources.

- [ ] **Step 3: Commit documentation update**

```bash
git add README.md docs/superpowers/specs/2026-05-23-puppyrun-public-demo-deployment-design.md docs/superpowers/plans/2026-05-23-puppyrun-public-demo-deployment-plan.md
git commit -m "docs: switch public demo plan to vps"
```

---

### Task 7: Full Local Verification

**Files:**
- No new files.

- [ ] **Step 1: Run backend lint and tests**

Run:

```bash
cd backend
. .venv/bin/activate
ruff check .
pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend tests and production build**

Run:

```bash
cd apps/web
npm test
npm run build
```

Expected: PASS.

- [ ] **Step 3: Run existing local Docker Compose build and health check**

Run:

```bash
docker compose up --build -d
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok","service":"puppyrun-api"}
```

- [ ] **Step 4: Verify local dummy Agent loop manually**

Run:

```bash
open http://localhost:5173
```

Then:

1. Create a decision session.
2. Click `Start dummy Agent run`.
3. Do not click `Refresh`.
4. Wait until the selected session detail panel shows `completed`.

Expected: the selected session detail panel updates to `completed` and shows the dummy Agent summary.

- [ ] **Step 5: Shut down local containers**

Run:

```bash
docker compose down
```

Expected: containers stop cleanly.

---

### Task 8: VPS Deployment And Public Verification

**Files:**
- Modify: `README.md` after the real public host is known.
- Modify: `deploy/vps/.env` on the VPS only; do not commit it.

**Execution note, 2026-05-26:** The first public demo was deployed as a temporary raw-IP HTTP demo because no domain is configured yet. The real IP, SSH username, and local key path are kept in private local notes rather than commit-bound repo docs. The VPS needed Docker Engine and the Docker Compose plugin installed. Docker Hub pulls from the VPS timed out, so the current local Compose images were built locally, transferred with `docker save | gzip | ssh docker load`, and started on the VPS with `docker compose -p puppyrun up --no-build --pull never -d`. Public `/health`, API session/run polling, and browser smoke verification passed. README records placeholder temporary HTTP URLs; no commit has been created because repo state changes still require explicit user authorization.

- [x] **Step 1: Stop for user authorization before touching the VPS**

Before using SSH or creating remote resources, explicitly confirm with the user:

```text
This deployment will use your VPS, open/serve public HTTP and HTTPS traffic, and run PostgreSQL and Redis containers on that server. Please provide the SSH target and public host, and confirm that I may deploy the PuppyRun Phase 0 public demo there.
```

Expected: proceed only after the user authorizes and provides the real SSH target and public host.

- [x] **Step 2: Verify DNS points to the VPS**

For the 2026-05-26 temporary raw-IP deployment, DNS was not applicable. The public host was a raw VPS IP; the real IP is kept in private local notes. Domain-backed HTTPS remains pending.

Run from the local machine after the user provides the host:

```bash
dig +short "$PUPPYRUN_PUBLIC_HOST"
```

Expected: output includes the VPS public IP address.

- [x] **Step 3: Deploy from a repository checkout on the VPS**

Run on the VPS from the repository root:

```bash
cp deploy/vps/.env.example deploy/vps/.env
```

Edit `deploy/vps/.env` so it contains the real public host and a strong database password.
`POSTGRES_PASSWORD` is the raw PostgreSQL password; `POSTGRES_PASSWORD_URLENCODED` is the URL-encoded form used in the API and worker database URL. Generate the encoded value with:

```bash
python3 - <<'PY'
from urllib.parse import quote
print(quote(input("PostgreSQL password: "), safe=""))
PY
```

Put the command output in `POSTGRES_PASSWORD_URLENCODED` and keep the original raw value in `POSTGRES_PASSWORD`.

Start services:

```bash
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml up --build -d
```

Expected: Docker Compose starts `postgres`, `redis`, `api`, `worker`, and `web`.

- [x] **Step 4: Check service logs**

Run on the VPS:

```bash
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml ps
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml logs --tail=100 api
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml logs --tail=100 worker
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml logs --tail=100 web
```

Expected: services are running, API logs show Alembic completed, worker logs show arq started, and web logs show Caddy serving without TLS errors.

- [x] **Step 5: Check public API health URL**

Run:

```bash
curl "https://${PUPPYRUN_PUBLIC_HOST}/health"
```

Expected:

```json
{"status":"ok","service":"puppyrun-api"}
```

- [x] **Step 6: Run public browser smoke test**

Open:

```bash
open "https://${PUPPYRUN_PUBLIC_HOST}"
```

Then:

1. Create a decision session.
2. Click `Start dummy Agent run`.
3. Do not click `Refresh`.
4. Wait until the selected session detail panel shows `completed`.

Expected: the selected session detail panel updates to `completed` and shows the dummy Agent summary.

- [ ] **Step 7: Record final public URLs**

Update `README.md` with the actual public host:

- Web URL: `https://${PUPPYRUN_PUBLIC_HOST}`
- API health URL: `https://${PUPPYRUN_PUBLIC_HOST}/health`

Then commit:

```bash
git add README.md
git commit -m "docs: record vps public demo urls"
```

Expected: README reflects the verified public deployment.

---

## Self-Review Notes

- Spec coverage:
  - Public Web URL: Task 2, Task 3, Task 8.
  - Public API URL and health check: Task 2, Task 3, Task 8.
  - VPS-hosted PostgreSQL and Redis: Task 2 and Task 8.
  - Worker process: Task 2 and Task 8.
  - Alembic migration on API startup: Task 2 and Task 8.
  - CORS and same-origin frontend API base URL: Task 2, Task 5, and Task 8.
  - Local Docker Compose compatibility: Task 4, Task 5, and Task 7.
  - Kubernetes portability: Task 2, Task 4, Task 5, and Task 6.
- Placeholder scan:
  - The plan uses environment variables for deployment-specific values.
  - Remote deployment is gated on user-provided SSH target and public host.
- Type consistency:
  - Existing `normalize_database_url`, `Settings.sqlalchemy_database_url`, and `settings.sqlalchemy_database_url` are preserved.
  - VPS service names are consistent across `docker-compose.yml`, `Caddyfile`, runbook, README, and smoke commands.
