# PuppyRun Public Demo Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Render-based public demo deployment path that proves the Phase 0 Web -> API -> PostgreSQL -> Redis/arq worker -> Web polling loop online while keeping the app portable for later VPS and Kubernetes deployment.

**Architecture:** Keep Render-specific behavior in `render.yaml` and documentation. Keep application runtime portable through Docker images, environment variables, PostgreSQL, Redis, Alembic migrations, a standard FastAPI process, and a standard arq worker process. Do not add real Agent logic or VPS/Kubernetes manifests in this plan.

**Tech Stack:** Render Blueprint, Docker, FastAPI, SQLAlchemy async, Alembic, PostgreSQL, Render Key Value Redis-compatible queue, arq, React/Vite static build, pytest, ruff, npm.

---

## File Structure

Create and modify these files:

- Create: `backend/tests/test_config.py`
  - Verifies database URL normalization and platform `PORT` handling.
- Modify: `backend/puppyrun_api/config.py`
  - Adds portable database URL normalization and `PORT` fallback support.
- Modify: `backend/puppyrun_api/db.py`
  - Uses the normalized SQLAlchemy async database URL.
- Modify: `backend/Dockerfile`
  - Installs production backend dependencies and keeps the image usable for API or worker commands.
- Create: `render.yaml`
  - Declares the Render static site, API web service, worker, PostgreSQL, and Key Value queue.
- Modify: `README.md`
  - Documents the Render public demo topology, smoke tests, portability boundary, and disposable demo data warning.

Do not modify Phase 1 Agent workflow code in this plan.

---

### Task 1: Backend Configuration Compatibility Tests

**Files:**
- Create: `backend/tests/test_config.py`

- [ ] **Step 1: Add failing tests for PostgreSQL URL normalization and platform port handling**

Create `backend/tests/test_config.py`:

```python
from puppyrun_api.config import Settings, normalize_database_url


def test_normalize_database_url_keeps_asyncpg_url() -> None:
    url = "postgresql+asyncpg://user:pass@postgres:5432/puppyrun"

    assert normalize_database_url(url) == url


def test_normalize_database_url_converts_postgresql_url() -> None:
    url = "postgresql://user:pass@postgres:5432/puppyrun"

    assert normalize_database_url(url) == "postgresql+asyncpg://user:pass@postgres:5432/puppyrun"


def test_normalize_database_url_converts_legacy_postgres_url() -> None:
    url = "postgres://user:pass@postgres:5432/puppyrun"

    assert normalize_database_url(url) == "postgresql+asyncpg://user:pass@postgres:5432/puppyrun"


def test_normalize_database_url_leaves_non_postgres_url_unchanged() -> None:
    url = "sqlite+aiosqlite:///:memory:"

    assert normalize_database_url(url) == url


def test_settings_reads_platform_port(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.delenv("PUPPYRUN_API_PORT", raising=False)

    settings = Settings()

    assert settings.api_port == 10000


def test_settings_prefers_explicit_puppyrun_api_port(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("PUPPYRUN_API_PORT", "9000")

    settings = Settings()

    assert settings.api_port == 9000


def test_settings_exposes_normalized_sqlalchemy_database_url(monkeypatch) -> None:
    monkeypatch.setenv("PUPPYRUN_DATABASE_URL", "postgresql://user:pass@postgres:5432/puppyrun")

    settings = Settings()

    assert settings.sqlalchemy_database_url == (
        "postgresql+asyncpg://user:pass@postgres:5432/puppyrun"
    )
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_config.py -q
```

Expected: FAIL because `normalize_database_url` and `Settings.sqlalchemy_database_url` do not exist yet.

- [ ] **Step 3: Commit the failing test**

```bash
git add backend/tests/test_config.py
git commit -m "test: cover deployment config compatibility"
```

---

### Task 2: Backend Runtime Port And Database URL Compatibility

**Files:**
- Modify: `backend/puppyrun_api/config.py`
- Modify: `backend/puppyrun_api/db.py`

- [ ] **Step 1: Implement configuration compatibility**

Replace `backend/puppyrun_api/config.py` with:

```python
from functools import lru_cache

from pydantic import AliasChoices, AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return f"postgresql+asyncpg://{url.removeprefix('postgresql://')}"
    if url.startswith("postgres://"):
        return f"postgresql+asyncpg://{url.removeprefix('postgres://')}"
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PUPPYRUN_", env_file=".env", extra="ignore")

    env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("PUPPYRUN_API_PORT", "PORT"),
    )
    database_url: str = "postgresql+asyncpg://puppyrun:puppyrun@localhost:5432/puppyrun"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[AnyHttpUrl] = Field(default_factory=list)

    @property
    def sqlalchemy_database_url(self) -> str:
        return normalize_database_url(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 2: Use the normalized database URL in SQLAlchemy**

In `backend/puppyrun_api/db.py`, change `create_engine()` to:

```python
def create_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.sqlalchemy_database_url, pool_pre_ping=True)
```

- [ ] **Step 3: Run the focused config tests**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 4: Run backend lint and tests**

Run:

```bash
cd backend
. .venv/bin/activate
ruff check .
pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit backend compatibility changes**

```bash
git add backend/puppyrun_api/config.py backend/puppyrun_api/db.py
git commit -m "fix: normalize hosted deployment settings"
```

---

### Task 3: Portable Backend Docker Image

**Files:**
- Modify: `backend/Dockerfile`

- [ ] **Step 1: Update the backend Dockerfile for production-style dependency install and portable default command**

Replace `backend/Dockerfile` with:

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations
COPY puppyrun_api /app/puppyrun_api
COPY puppyrun_worker /app/puppyrun_worker

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

CMD ["sh", "-c", "uvicorn puppyrun_api.main:app --host ${PUPPYRUN_API_HOST:-0.0.0.0} --port ${PORT:-${PUPPYRUN_API_PORT:-8000}}"]
```

- [ ] **Step 2: Build the backend image locally**

Run:

```bash
docker build -t puppyrun-backend:phase0 ./backend
```

Expected: image builds successfully and installs `puppyrun-backend` without `.[dev]`.

- [ ] **Step 3: Run backend lint and tests again**

Run:

```bash
cd backend
. .venv/bin/activate
ruff check .
pytest -q
```

Expected: PASS.

- [ ] **Step 4: Commit Dockerfile update**

```bash
git add backend/Dockerfile
git commit -m "chore: make backend image deployment-ready"
```

---

### Task 4: Render Blueprint

**Files:**
- Create: `render.yaml`

- [ ] **Step 1: Add Render Blueprint configuration**

Create `render.yaml` at the repository root:

```yaml
services:
  - type: keyvalue
    name: puppyrun-phase0-queue
    plan: free
    ipAllowList: []
    maxmemoryPolicy: noeviction

  - type: web
    name: puppyrun-phase0-web
    runtime: static
    buildCommand: cd apps/web && npm install && npm run build
    staticPublishPath: apps/web/dist
    routes:
      - type: rewrite
        source: /*
        destination: /index.html
    envVars:
      - key: VITE_API_BASE_URL
        value: https://puppyrun-phase0-api.onrender.com

  - type: web
    name: puppyrun-phase0-api
    runtime: docker
    plan: free
    dockerfilePath: ./backend/Dockerfile
    dockerContext: ./backend
    dockerCommand: sh -c "alembic upgrade head && uvicorn puppyrun_api.main:app --host 0.0.0.0 --port ${PORT:-8000}"
    healthCheckPath: /health
    envVars:
      - key: PUPPYRUN_ENV
        value: production
      - key: PUPPYRUN_DATABASE_URL
        fromDatabase:
          name: puppyrun-phase0-db
          property: connectionString
      - key: PUPPYRUN_REDIS_URL
        fromService:
          type: keyvalue
          name: puppyrun-phase0-queue
          property: connectionString
      - key: PUPPYRUN_CORS_ORIGINS
        value: '["https://puppyrun-phase0-web.onrender.com"]'

  - type: worker
    name: puppyrun-phase0-worker
    runtime: docker
    plan: starter
    dockerfilePath: ./backend/Dockerfile
    dockerContext: ./backend
    dockerCommand: arq puppyrun_worker.main.WorkerSettings
    envVars:
      - key: PUPPYRUN_ENV
        value: production
      - key: PUPPYRUN_DATABASE_URL
        fromDatabase:
          name: puppyrun-phase0-db
          property: connectionString
      - key: PUPPYRUN_REDIS_URL
        fromService:
          type: keyvalue
          name: puppyrun-phase0-queue
          property: connectionString

databases:
  - name: puppyrun-phase0-db
    plan: free
    databaseName: puppyrun
    user: puppyrun
    ipAllowList: []
```

- [ ] **Step 2: Validate static web build locally**

Run:

```bash
cd apps/web
npm test
npm run build
```

Expected: PASS and `apps/web/dist` is created.

- [ ] **Step 3: Validate backend Docker build after Blueprint paths are added**

Run:

```bash
docker build -t puppyrun-backend:render ./backend
```

Expected: PASS.

- [ ] **Step 4: Commit Render Blueprint**

```bash
git add render.yaml
git commit -m "chore: add render public demo blueprint"
```

---

### Task 5: Public Demo Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add public demo deployment documentation**

In `README.md`, add this section after the existing "Public URL status" section:

````markdown
## Public Demo Deployment

Phase 0 public demo deployment uses Render Blueprint as a short-term public URL adapter.
The final production-oriented deployment target remains VPS and Kubernetes, so application
code must stay portable across Docker, environment variables, PostgreSQL, Redis, and Alembic.

Render services declared in `render.yaml`:

- `puppyrun-phase0-web`: static React/Vite web console.
- `puppyrun-phase0-api`: FastAPI API web service.
- `puppyrun-phase0-worker`: arq background worker.
- `puppyrun-phase0-db`: PostgreSQL database.
- `puppyrun-phase0-queue`: Redis-compatible Render Key Value queue.

Expected Render URLs:

- Web: `https://puppyrun-phase0-web.onrender.com`
- API health: `https://puppyrun-phase0-api.onrender.com/health`

If Render assigns different public subdomains, update both `render.yaml` and this README before
using the public smoke test as Phase 0 evidence.

Public demo data is disposable. Anyone with the URL can create demo sessions, so do not enter
private prompts, secrets, credentials, or confidential project details.

### Public Smoke Test

1. Open `https://puppyrun-phase0-web.onrender.com`.
2. Create a decision session.
3. Click `Start dummy Agent run`.
4. Do not click `Refresh`.
5. Wait until the selected session detail panel shows `completed`.
6. Open `https://puppyrun-phase0-api.onrender.com/health` and confirm:

```json
{"status":"ok","service":"puppyrun-api"}
```

Phase 0 public URL verification passes only when the public web page and hosted async worker loop
both work through hosted PostgreSQL and Redis.
````

- [ ] **Step 2: Run Markdown sanity check**

Run:

```bash
python - <<'PY'
from pathlib import Path

needles = ["TO" + "DO", "TB" + "D", "fill " + "in", "coming " + "soon"]
paths = [Path("README.md"), Path("docs/superpowers/specs"), Path("docs/superpowers/plans")]
matches = []
for path in paths:
    files = [path] if path.is_file() else sorted(path.rglob("*.md"))
    for file_path in files:
        for line_number, line in enumerate(file_path.read_text().splitlines(), start=1):
            if any(needle in line for needle in needles):
                matches.append(f"{file_path}:{line_number}:{line}")
if matches:
    raise SystemExit("\n".join(matches))
PY
```

Expected: no matches related to the public demo deployment plan. Existing historical wording unrelated to this deployment can remain if it is part of a prior committed spec.

- [ ] **Step 3: Commit documentation update**

```bash
git add README.md
git commit -m "docs: document public demo deployment"
```

---

### Task 6: Full Local Verification

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

- [ ] **Step 3: Run Docker Compose build and health check**

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

### Task 7: Render Deployment And Public Verification

**Files:**
- Modify: `README.md` only if Render assigns URLs different from the expected `puppyrun-phase0-web` or `puppyrun-phase0-api` subdomains.
- Modify: `render.yaml` only if Render assigns URLs different from the expected `puppyrun-phase0-web` or `puppyrun-phase0-api` subdomains.

- [ ] **Step 1: Stop for user authorization before creating Render resources**

Before creating the Render Blueprint, explicitly confirm with the user:

```text
This deployment can create hosted Render resources. The worker service uses the `starter` plan in render.yaml because Render background workers do not use the web-service free plan. Do you authorize creating the Render Blueprint resources for the Phase 0 public demo?
```

Expected: proceed only after the user authorizes.

- [ ] **Step 2: Create the Render Blueprint**

In the Render dashboard:

1. Choose Blueprint.
2. Connect the PuppyRun Git repository.
3. Select the branch that contains this plan and `render.yaml`.
4. Use the repository root `render.yaml`.
5. Confirm the services and database listed in the preview match:
   - `puppyrun-phase0-web`
   - `puppyrun-phase0-api`
   - `puppyrun-phase0-worker`
   - `puppyrun-phase0-db`
   - `puppyrun-phase0-queue`
6. Create the Blueprint.

Expected: Render starts builds for the web, API, and worker services and provisions PostgreSQL and Key Value.

- [ ] **Step 3: Check API health URL**

Run:

```bash
curl https://puppyrun-phase0-api.onrender.com/health
```

Expected:

```json
{"status":"ok","service":"puppyrun-api"}
```

- [ ] **Step 4: Run public browser smoke test**

Open:

```bash
open https://puppyrun-phase0-web.onrender.com
```

Then:

1. Create a decision session.
2. Click `Start dummy Agent run`.
3. Do not click `Refresh`.
4. Wait until the selected session detail panel shows `completed`.

Expected: the selected session detail panel updates to `completed` and shows the dummy Agent summary.

- [ ] **Step 5: If Render assigned different public URLs, update configuration and docs**

If the actual public URLs differ from `https://puppyrun-phase0-web.onrender.com` or `https://puppyrun-phase0-api.onrender.com`, update:

- `render.yaml`:
  - `VITE_API_BASE_URL`
  - `PUPPYRUN_CORS_ORIGINS`
- `README.md`:
  - Web URL
  - API health URL
  - public smoke test URLs

Then commit:

```bash
git add render.yaml README.md
git commit -m "docs: record render public demo urls"
```

Expected: public smoke test passes with the actual Render URLs.

---

## Self-Review Notes

- Spec coverage:
  - Public Web URL: Task 4 and Task 7.
  - Public API URL and health check: Task 4 and Task 7.
  - Hosted PostgreSQL and Redis-compatible queue: Task 4.
  - Worker process: Task 4 and Task 7.
  - Alembic migration on API startup: Task 4.
  - CORS and Vite API base URL: Task 4 and Task 5.
  - Local Docker Compose compatibility: Task 2, Task 3, and Task 6.
  - VPS/Kubernetes portability: Task 2, Task 3, Task 4, and Task 5.
- Placeholder scan:
  - The plan uses concrete service names and expected URLs.
  - The only conditional branch is the explicit Render URL correction step after deployment.
- Type consistency:
  - `normalize_database_url`, `Settings.sqlalchemy_database_url`, and `settings.sqlalchemy_database_url` are introduced before use.
  - Render service names are consistent across `render.yaml`, README, and public smoke commands.
