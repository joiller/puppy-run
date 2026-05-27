# PuppyRun Phase 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deployable PuppyRun skeleton with a FastAPI backend, PostgreSQL, Redis-backed worker, React/Vite web console, Docker Compose, health checks, and a dummy Agent job that updates a decision session.

**Architecture:** Use a modular monolith: one Python backend package exposes HTTP APIs and shared domain/database code, while a separate worker process imports the same package and executes background jobs. The first client is a lightweight web console that calls the API and observes session status. Phase 0 intentionally avoids real LLM calls and real Agent research logic; it proves the deployment shape, state flow, and async job path.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL, Redis, arq, pytest, React, TypeScript, Vite, Docker Compose.

**Closure status, 2026-05-27:** Phase 0 is closed at the repository scope. The local deployable skeleton was implemented and merged into `main`; the selected-session polling gap was fixed; and the VPS public demo loop was verified through a temporary raw-IP HTTP deployment on 2026-05-26. Real public URLs, VPS IPs, SSH targets, and secrets are intentionally not recorded in repository docs.

---

## Scope Check

The approved spec covers a large platform. This plan only implements Phase 0: Deployable Skeleton. It creates the repo shape and a thin vertical path:

```text
Web console -> FastAPI -> PostgreSQL session row -> Redis/arq job -> worker -> PostgreSQL status update -> Web console status refresh
```

The following spec sections are deliberately not implemented in Phase 0: real candidate discovery, real evidence collection, real scoring, real MCP adapters, real eval dashboard, and production hardening beyond basic health checks and Docker Compose.

## File Structure

Create this structure:

```text
puppy-run/
  .env.example
  .gitignore
  docker-compose.yml
  README.md
  docs/superpowers/specs/2026-05-21-puppyrun-design.md
  docs/superpowers/plans/2026-05-21-puppyrun-phase-0-plan.md
  backend/
    Dockerfile
    alembic.ini
    pyproject.toml
    migrations/
      env.py
      script.py.mako
      versions/
        0001_phase0_sessions.py
    puppyrun_api/
      __init__.py
      config.py
      db.py
      main.py
      models.py
      schemas.py
      repositories/
        __init__.py
        sessions.py
      routes/
        __init__.py
        health.py
        sessions.py
    puppyrun_worker/
      __init__.py
      jobs.py
      main.py
    tests/
      conftest.py
      test_health.py
      test_sessions.py
      test_worker_jobs.py
  apps/
    web/
      Dockerfile
      index.html
      package.json
      tsconfig.json
      vite.config.ts
      src/
        App.css
        App.tsx
        api.ts
        main.tsx
        types.ts
  .github/
    workflows/
      ci.yml
```

Responsibility boundaries:

- `backend/puppyrun_api`: HTTP API, database models, repositories, schemas.
- `backend/puppyrun_worker`: Redis/arq worker entrypoint and jobs.
- `apps/web`: first client, a web console shell.
- `docker-compose.yml`: local self-host orchestration.
- `.github/workflows/ci.yml`: verifies backend tests and frontend build.

## Phase 0 Decisions

- Use PostgreSQL as the primary database.
- Use Redis for queue, cache, and future rate limits.
- Use arq for the first worker because it is asyncio-native and lightweight.
- Use FastAPI only as the HTTP/API framework. It is not the Agent runtime.
- Implement a minimal PuppyRun runtime path ourselves in Phase 0 rather than binding the core to LangChain, LangGraph, CrewAI, or OpenAI Agents SDK.
- Use React + Vite + TypeScript for the initial web console because it is a small SPA and does not need server-side rendering in Phase 0.

---

### Task 1: Repository Metadata And Environment Contract

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Add `.gitignore`**

Create `.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.mypy_cache/
.venv/
dist/
build/
*.egg-info/

# Node
node_modules/
apps/web/dist/

# Environment
.env
.env.*
!.env.example

# OS/editor
.DS_Store
.idea/
.vscode/

# Runtime
*.log
```

- [ ] **Step 2: Add `.env.example`**

Create `.env.example`:

```dotenv
PUPPYRUN_ENV=development
PUPPYRUN_API_HOST=0.0.0.0
PUPPYRUN_API_PORT=8000
PUPPYRUN_DATABASE_URL=postgresql+asyncpg://puppyrun:puppyrun@localhost:5432/puppyrun
PUPPYRUN_REDIS_URL=redis://localhost:6379/0
PUPPYRUN_CORS_ORIGINS=["http://localhost:5173"]
VITE_API_BASE_URL=http://localhost:8000
```

- [ ] **Step 3: Update `README.md` with Phase 0 run contract**

Replace the existing README with:

```markdown
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
```

- [ ] **Step 4: Verify metadata files**

Run:

```bash
test -f .gitignore && test -f .env.example && test -f README.md
```

Expected: command exits with status `0`.

- [ ] **Step 5: Commit**

```bash
git add .gitignore .env.example README.md
git commit -m "chore: add project metadata"
```

---

### Task 2: Backend Package And Health API

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/puppyrun_api/__init__.py`
- Create: `backend/puppyrun_api/config.py`
- Create: `backend/puppyrun_api/main.py`
- Create: `backend/puppyrun_api/routes/__init__.py`
- Create: `backend/puppyrun_api/routes/health.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health.py`

- [ ] **Step 1: Create backend package metadata**

Create `backend/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69.0"]
build-backend = "setuptools.build_meta"

[project]
name = "puppyrun-backend"
version = "0.1.0"
description = "PuppyRun API and worker"
requires-python = ">=3.12"
dependencies = [
  "alembic>=1.13.0",
  "arq>=0.26.0",
  "asyncpg>=0.29.0",
  "fastapi>=0.115.0",
  "greenlet>=3.0.0",
  "python-dotenv>=1.0.0",
  "pydantic-settings>=2.6.0",
  "sqlalchemy>=2.0.0",
  "uvicorn[standard]>=0.32.0",
]

[project.optional-dependencies]
dev = [
  "httpx>=0.27.0",
  "pytest>=8.0.0",
  "pytest-asyncio>=0.24.0",
  "ruff>=0.8.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.setuptools.packages.find]
where = ["."]
include = ["puppyrun_api*", "puppyrun_worker*"]
```

- [ ] **Step 2: Add settings**

Create `backend/puppyrun_api/config.py`:

```python
from functools import lru_cache

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PUPPYRUN_", env_file=".env", extra="ignore")

    env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "postgresql+asyncpg://puppyrun:puppyrun@localhost:5432/puppyrun"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[AnyHttpUrl] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: Add FastAPI app and health route**

Create `backend/puppyrun_api/routes/health.py`:

```python
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "puppyrun-api"}
```

Create `backend/puppyrun_api/routes/__init__.py`:

```python
from puppyrun_api.routes import health

__all__ = ["health"]
```

Create `backend/puppyrun_api/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from puppyrun_api.config import get_settings
from puppyrun_api.routes import health


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PuppyRun API", version="0.1.0")

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin).rstrip("/") for origin in settings.cors_origins],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    return app


app = create_app()
```

Create `backend/puppyrun_api/__init__.py`:

```python
__all__ = ["main"]
```

- [ ] **Step 4: Add health test**

Create `backend/tests/conftest.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from puppyrun_api.main import create_app


@pytest.fixture
async def api_client() -> AsyncClient:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
```

Create `backend/tests/test_health.py`:

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok(api_client: AsyncClient) -> None:
    response = await api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "puppyrun-api"}
```

- [ ] **Step 5: Run backend test**

Run:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest tests/test_health.py -q
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/puppyrun_api backend/tests
git commit -m "feat: add FastAPI health endpoint"
```

---

### Task 3: Database Models, Alembic, And Session Repository

**Files:**
- Create: `backend/puppyrun_api/db.py`
- Create: `backend/puppyrun_api/models.py`
- Create: `backend/puppyrun_api/schemas.py`
- Create: `backend/puppyrun_api/repositories/__init__.py`
- Create: `backend/puppyrun_api/repositories/sessions.py`
- Create: `backend/alembic.ini`
- Create: `backend/migrations/env.py`
- Create: `backend/migrations/script.py.mako`
- Create: `backend/migrations/versions/0001_phase0_sessions.py`

- [ ] **Step 1: Add async database setup**

Create `backend/puppyrun_api/db.py`:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from puppyrun_api.config import get_settings


class Base(DeclarativeBase):
    pass


def create_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True)


engine = create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

- [ ] **Step 2: Add Phase 0 models**

Create `backend/puppyrun_api/models.py`:

```python
import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from puppyrun_api.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class DecisionSessionStatus(str, enum.Enum):
    created = "created"
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AgentRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class DecisionSession(Base):
    __tablename__ = "decision_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DecisionSessionStatus] = mapped_column(
        Enum(DecisionSessionStatus, name="decision_session_status"),
        default=DecisionSessionStatus.created,
        nullable=False,
    )
    current_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("decision_sessions.id"), nullable=False
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, name="agent_run_status"),
        default=AgentRunStatus.queued,
        nullable=False,
    )
    job_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    session: Mapped[DecisionSession] = relationship(back_populates="agent_runs")
    events: Mapped[list["AgentEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[AgentRun] = relationship(back_populates="events")
```

- [ ] **Step 3: Add schemas**

Create `backend/puppyrun_api/schemas.py`:

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from puppyrun_api.models import AgentRunStatus, DecisionSessionStatus


class CreateDecisionSessionRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=4000)


class DecisionSessionResponse(BaseModel):
    id: UUID
    title: str
    prompt: str
    status: DecisionSessionStatus
    current_summary: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentRunResponse(BaseModel):
    id: UUID
    session_id: UUID
    status: AgentRunStatus
    job_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentEventResponse(BaseModel):
    id: UUID
    run_id: UUID
    event_type: str
    message: str
    payload: dict
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Add repository functions**

Create `backend/puppyrun_api/repositories/__init__.py`:

```python
from puppyrun_api.repositories import sessions

__all__ = ["sessions"]
```

Create `backend/puppyrun_api/repositories/sessions.py`:

```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_api.models import (
    AgentEvent,
    AgentRun,
    AgentRunStatus,
    DecisionSession,
    DecisionSessionStatus,
)


def derive_title(prompt: str) -> str:
    compact = " ".join(prompt.split())
    return compact[:80] if len(compact) > 80 else compact


async def create_decision_session(db: AsyncSession, prompt: str) -> DecisionSession:
    session = DecisionSession(title=derive_title(prompt), prompt=prompt)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_decision_sessions(db: AsyncSession) -> list[DecisionSession]:
    result = await db.execute(select(DecisionSession).order_by(DecisionSession.created_at.desc()))
    return list(result.scalars())


async def get_decision_session(db: AsyncSession, session_id: UUID) -> DecisionSession | None:
    return await db.get(DecisionSession, session_id)


async def create_agent_run(db: AsyncSession, session_id: UUID) -> AgentRun:
    run = AgentRun(session_id=session_id, status=AgentRunStatus.queued)
    session = await db.get(DecisionSession, session_id)
    if session is not None:
        session.status = DecisionSessionStatus.queued
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def mark_run_started(db: AsyncSession, run_id: UUID) -> None:
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"agent run not found: {run_id}")
    session = await db.get(DecisionSession, run.session_id)
    run.status = AgentRunStatus.running
    if session is not None:
        session.status = DecisionSessionStatus.running
    db.add(AgentEvent(run_id=run.id, event_type="run_started", message="Dummy Agent run started"))
    await db.commit()


async def mark_run_completed(db: AsyncSession, run_id: UUID, summary: str) -> None:
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"agent run not found: {run_id}")
    session = await db.get(DecisionSession, run.session_id)
    run.status = AgentRunStatus.completed
    if session is not None:
        session.status = DecisionSessionStatus.completed
        session.current_summary = summary
    db.add(
        AgentEvent(
            run_id=run.id,
            event_type="run_completed",
            message="Dummy Agent run completed",
            payload={"summary": summary},
        )
    )
    await db.commit()
```

- [ ] **Step 5: Add Alembic files**

Create `backend/alembic.ini`:

```ini
[alembic]
script_location = migrations
prepend_sys_path = .
sqlalchemy.url = postgresql+asyncpg://puppyrun:puppyrun@localhost:5432/puppyrun

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

Create `backend/migrations/env.py`:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from puppyrun_api.config import get_settings
from puppyrun_api.db import Base
from puppyrun_api import models  # noqa: F401

config = context.config
fileConfig(config.config_file_name)
target_metadata = Base.metadata


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio

    asyncio.run(run_migrations_online())
```

Create `backend/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create `backend/migrations/versions/0001_phase0_sessions.py`:

```python
"""create phase0 session tables

Revision ID: 0001_phase0_sessions
Revises:
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_phase0_sessions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    decision_status = sa.Enum(
        "created",
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        name="decision_session_status",
    )
    run_status = sa.Enum("queued", "running", "completed", "failed", name="agent_run_status")
    op.create_table(
        "decision_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", decision_status, nullable=False),
        sa.Column("current_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("job_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["decision_sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "agent_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("agent_events")
    op.drop_table("agent_runs")
    op.drop_table("decision_sessions")
    sa.Enum(name="agent_run_status").drop(op.get_bind())
    sa.Enum(name="decision_session_status").drop(op.get_bind())
```

- [ ] **Step 6: Commit**

```bash
git add backend/puppyrun_api/db.py backend/puppyrun_api/models.py backend/puppyrun_api/schemas.py backend/puppyrun_api/repositories backend/alembic.ini backend/migrations
git commit -m "feat: add phase zero database model"
```

---

### Task 4: Decision Session API

**Files:**
- Create: `backend/puppyrun_api/routes/sessions.py`
- Modify: `backend/puppyrun_api/routes/__init__.py`
- Modify: `backend/puppyrun_api/main.py`
- Create: `backend/tests/test_sessions.py`

- [ ] **Step 1: Add session routes**

Create `backend/puppyrun_api/routes/sessions.py`:

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_api.db import get_session
from puppyrun_api.repositories import sessions as session_repo
from puppyrun_api.schemas import CreateDecisionSessionRequest, DecisionSessionResponse

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", response_model=DecisionSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateDecisionSessionRequest,
    db: AsyncSession = Depends(get_session),
) -> DecisionSessionResponse:
    session = await session_repo.create_decision_session(db, body.prompt)
    return DecisionSessionResponse.model_validate(session)


@router.get("", response_model=list[DecisionSessionResponse])
async def list_sessions(db: AsyncSession = Depends(get_session)) -> list[DecisionSessionResponse]:
    sessions = await session_repo.list_decision_sessions(db)
    return [DecisionSessionResponse.model_validate(session) for session in sessions]


@router.get("/{session_id}", response_model=DecisionSessionResponse)
async def get_session_by_id(
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> DecisionSessionResponse:
    session = await session_repo.get_decision_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="decision session not found")
    return DecisionSessionResponse.model_validate(session)
```

- [ ] **Step 2: Register routes**

Update `backend/puppyrun_api/routes/__init__.py`:

```python
from puppyrun_api.routes import health, sessions

__all__ = ["health", "sessions"]
```

Update `backend/puppyrun_api/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from puppyrun_api.config import get_settings
from puppyrun_api.routes import health, sessions


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PuppyRun API", version="0.1.0")

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin).rstrip("/") for origin in settings.cors_origins],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    app.include_router(sessions.router)
    return app


app = create_app()
```

- [ ] **Step 3: Add route tests with dependency override**

Create `backend/tests/test_sessions.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_api.db import Base, get_session
from puppyrun_api.main import create_app


@pytest.fixture
async def session_client() -> AsyncClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_get_session(session_client: AsyncClient) -> None:
    create_response = await session_client.post(
        "/api/v1/sessions",
        json={"prompt": "Compare LangGraph and OpenAI Agents SDK for a stateful Agent runtime."},
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["status"] == "created"
    assert created["title"].startswith("Compare LangGraph")

    get_response = await session_client.get(f"/api/v1/sessions/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_create_session_rejects_short_prompt(session_client: AsyncClient) -> None:
    response = await session_client.post("/api/v1/sessions", json={"prompt": "short"})

    assert response.status_code == 422
```

- [ ] **Step 4: Add SQLite test dependency**

Modify `backend/pyproject.toml` dev dependencies to include:

```toml
  "aiosqlite>=0.20.0",
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd backend
. .venv/bin/activate
python -m pip install -e ".[dev]"
pytest tests/test_health.py tests/test_sessions.py -q
```

Expected: health and session tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/puppyrun_api/routes backend/puppyrun_api/main.py backend/tests/test_sessions.py
git commit -m "feat: add decision session API"
```

---

### Task 5: arq Worker And Dummy Agent Job

**Files:**
- Create: `backend/puppyrun_worker/__init__.py`
- Create: `backend/puppyrun_worker/jobs.py`
- Create: `backend/puppyrun_worker/main.py`
- Modify: `backend/puppyrun_api/routes/sessions.py`
- Modify: `backend/puppyrun_api/schemas.py`
- Create: `backend/tests/test_worker_jobs.py`

- [ ] **Step 1: Add worker job**

Create `backend/puppyrun_worker/jobs.py`:

```python
import asyncio
from uuid import UUID

from puppyrun_api.db import SessionLocal
from puppyrun_api.repositories import sessions as session_repo


async def run_dummy_agent_job(ctx: dict, run_id: str) -> str:
    parsed_run_id = UUID(run_id)
    async with SessionLocal() as db:
        await session_repo.mark_run_started(db, parsed_run_id)
    await asyncio.sleep(0.1)
    summary = "Phase 0 dummy Agent completed. Real research workflow is not enabled yet."
    async with SessionLocal() as db:
        await session_repo.mark_run_completed(db, parsed_run_id, summary)
    return summary
```

Create `backend/puppyrun_worker/main.py`:

```python
from arq.connections import RedisSettings

from puppyrun_api.config import get_settings
from puppyrun_worker.jobs import run_dummy_agent_job


def redis_settings_from_url(url: str) -> RedisSettings:
    if not url.startswith("redis://"):
        raise ValueError("PUPPYRUN_REDIS_URL must start with redis://")
    without_scheme = url.removeprefix("redis://")
    host_port, _, database = without_scheme.partition("/")
    host, _, port = host_port.partition(":")
    return RedisSettings(host=host, port=int(port or "6379"), database=int(database or "0"))


class WorkerSettings:
    functions = [run_dummy_agent_job]
    redis_settings = redis_settings_from_url(get_settings().redis_url)
```

Create `backend/puppyrun_worker/__init__.py`:

```python
__all__ = ["jobs", "main"]
```

- [ ] **Step 2: Add run response schema**

Modify `backend/puppyrun_api/schemas.py` to include:

```python
class StartAgentRunResponse(BaseModel):
    session: DecisionSessionResponse
    run: AgentRunResponse
```

- [ ] **Step 3: Replace session routes with run enqueue support**

Replace `backend/puppyrun_api/routes/sessions.py` with:

```python
from uuid import UUID

from arq import create_pool
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from puppyrun_api.config import get_settings
from puppyrun_api.db import get_session
from puppyrun_api.repositories import sessions as session_repo
from puppyrun_api.schemas import (
    AgentRunResponse,
    CreateDecisionSessionRequest,
    DecisionSessionResponse,
    StartAgentRunResponse,
)
from puppyrun_worker.main import redis_settings_from_url

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", response_model=DecisionSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateDecisionSessionRequest,
    db: AsyncSession = Depends(get_session),
) -> DecisionSessionResponse:
    session = await session_repo.create_decision_session(db, body.prompt)
    return DecisionSessionResponse.model_validate(session)


@router.get("", response_model=list[DecisionSessionResponse])
async def list_sessions(db: AsyncSession = Depends(get_session)) -> list[DecisionSessionResponse]:
    sessions = await session_repo.list_decision_sessions(db)
    return [DecisionSessionResponse.model_validate(session) for session in sessions]


@router.get("/{session_id}", response_model=DecisionSessionResponse)
async def get_session_by_id(
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> DecisionSessionResponse:
    session = await session_repo.get_decision_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="decision session not found")
    return DecisionSessionResponse.model_validate(session)


@router.post("/{session_id}/runs", response_model=StartAgentRunResponse, status_code=202)
async def start_dummy_run(
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> StartAgentRunResponse:
    session = await session_repo.get_decision_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="decision session not found")

    run = await session_repo.create_agent_run(db, session_id)
    redis = await create_pool(redis_settings_from_url(get_settings().redis_url))
    try:
        job = await redis.enqueue_job("run_dummy_agent_job", str(run.id), _job_id=f"dummy:{run.id}")
        run.job_id = job.job_id if job is not None else f"dummy:{run.id}"
    finally:
        await redis.close()
    await db.commit()
    await db.refresh(run)
    await db.refresh(session)
    return StartAgentRunResponse(
        session=DecisionSessionResponse.model_validate(session),
        run=AgentRunResponse.model_validate(run),
    )
```

- [ ] **Step 4: Add unit test for direct job behavior**

Create `backend/tests/test_worker_jobs.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from puppyrun_api.db import Base
from puppyrun_api.models import DecisionSessionStatus
from puppyrun_api.repositories.sessions import create_agent_run, create_decision_session
from puppyrun_worker import jobs


@pytest.mark.asyncio
async def test_dummy_agent_job_marks_session_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(jobs, "SessionLocal", maker)

    async with maker() as db:
        session = await create_decision_session(
            db, "Compare LangGraph and OpenAI Agents SDK for PuppyRun."
        )
        run = await create_agent_run(db, session.id)
        run_id = run.id
        session_id = session.id

    await jobs.run_dummy_agent_job({}, str(run_id))

    async with maker() as db:
        refreshed = await db.get(type(session), session_id)
        assert refreshed is not None
        assert refreshed.status == DecisionSessionStatus.completed
        assert refreshed.current_summary == (
            "Phase 0 dummy Agent completed. Real research workflow is not enabled yet."
        )

    await engine.dispose()
```

- [ ] **Step 5: Run worker unit test**

Run:

```bash
cd backend
. .venv/bin/activate
pytest tests/test_worker_jobs.py -q
```

Expected: dummy worker job test passes.

- [ ] **Step 6: Commit**

```bash
git add backend/puppyrun_worker backend/puppyrun_api/routes/sessions.py backend/puppyrun_api/schemas.py backend/tests/test_worker_jobs.py
git commit -m "feat: add dummy agent worker job"
```

---

### Task 6: Web Console Shell

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/index.html`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/App.tsx`
- Create: `apps/web/src/App.css`
- Create: `apps/web/src/api.ts`
- Create: `apps/web/src/types.ts`

- [ ] **Step 1: Add frontend package**

Create `apps/web/package.json`:

```json
{
  "name": "@puppyrun/web",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite --host 0.0.0.0",
    "build": "tsc && vite build",
    "preview": "vite preview --host 0.0.0.0"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^5.0.0",
    "vite": "^7.0.0",
    "typescript": "^5.6.0"
  }
}
```

Create `apps/web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"]
}
```

Create `apps/web/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173
  }
});
```

Create `apps/web/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>PuppyRun</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 2: Add API client and types**

Create `apps/web/src/types.ts`:

```ts
export type DecisionSessionStatus =
  | "created"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface DecisionSession {
  id: string;
  title: string;
  prompt: string;
  status: DecisionSessionStatus;
  current_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface StartAgentRunResponse {
  session: DecisionSession;
  run: {
    id: string;
    session_id: string;
    status: string;
    job_id: string | null;
    created_at: string;
    updated_at: string;
  };
}
```

Create `apps/web/src/api.ts`:

```ts
import type { DecisionSession, StartAgentRunResponse } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function listSessions(): Promise<DecisionSession[]> {
  return request<DecisionSession[]>("/api/v1/sessions");
}

export async function createSession(prompt: string): Promise<DecisionSession> {
  return request<DecisionSession>("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ prompt })
  });
}

export async function startRun(sessionId: string): Promise<StartAgentRunResponse> {
  return request<StartAgentRunResponse>(`/api/v1/sessions/${sessionId}/runs`, {
    method: "POST"
  });
}
```

- [ ] **Step 3: Add web shell**

Create `apps/web/src/App.tsx`:

```tsx
import { FormEvent, useEffect, useState } from "react";
import { createSession, listSessions, startRun } from "./api";
import type { DecisionSession } from "./types";
import "./App.css";

const samplePrompt =
  "I want to build an Agent decision platform. Should I use LangGraph, OpenAI Agents SDK, CrewAI, AutoGen, or build a small runtime myself?";

export default function App() {
  const [prompt, setPrompt] = useState(samplePrompt);
  const [sessions, setSessions] = useState<DecisionSession[]>([]);
  const [selected, setSelected] = useState<DecisionSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  async function refreshSessions() {
    const items = await listSessions();
    setSessions(items);
    if (selected) {
      setSelected(items.find((item) => item.id === selected.id) ?? selected);
    }
  }

  useEffect(() => {
    refreshSessions().catch((err: unknown) => setError(String(err)));
    const timer = window.setInterval(() => {
      refreshSessions().catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, []);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setIsBusy(true);
    setError(null);
    try {
      const created = await createSession(prompt);
      setSelected(created);
      await refreshSessions();
    } catch (err) {
      setError(String(err));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleRun() {
    if (!selected) return;
    setIsBusy(true);
    setError(null);
    try {
      const result = await startRun(selected.id);
      setSelected(result.session);
      await refreshSessions();
    } catch (err) {
      setError(String(err));
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <section className="hero">
        <div>
          <p className="eyebrow">PuppyRun Phase 0</p>
          <h1>Agent decision workbench skeleton</h1>
          <p>
            Create a decision session, enqueue a dummy Agent run, and watch the backend update
            session state through the worker.
          </p>
        </div>
      </section>

      <section className="workspace">
        <form className="panel composer" onSubmit={handleCreate}>
          <label htmlFor="prompt">Decision prompt</label>
          <textarea id="prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} />
          <button disabled={isBusy || prompt.trim().length < 10} type="submit">
            Create session
          </button>
          {error && <p className="error">{error}</p>}
        </form>

        <section className="panel">
          <div className="panel-header">
            <h2>Sessions</h2>
            <button type="button" onClick={() => refreshSessions()} disabled={isBusy}>
              Refresh
            </button>
          </div>
          <div className="session-list">
            {sessions.map((session) => (
              <button
                className={selected?.id === session.id ? "session selected" : "session"}
                key={session.id}
                onClick={() => setSelected(session)}
                type="button"
              >
                <span>{session.title}</span>
                <strong>{session.status}</strong>
              </button>
            ))}
          </div>
        </section>

        <section className="panel detail">
          <div className="panel-header">
            <h2>Run status</h2>
            <button disabled={!selected || isBusy} onClick={handleRun} type="button">
              Start dummy Agent run
            </button>
          </div>
          {selected ? (
            <div>
              <p className="status">{selected.status}</p>
              <p>{selected.prompt}</p>
              {selected.current_summary && <p className="summary">{selected.current_summary}</p>}
            </div>
          ) : (
            <p>Select or create a session.</p>
          )}
        </section>
      </section>
    </main>
  );
}
```

Create `apps/web/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

Create `apps/web/src/App.css`:

```css
:root {
  color: #172026;
  background: #f5f7f8;
  font-family:
    Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

body {
  margin: 0;
}

button,
textarea {
  font: inherit;
}

.app-shell {
  min-height: 100vh;
}

.hero {
  background: #102026;
  color: white;
  padding: 40px;
}

.hero h1 {
  font-size: 40px;
  margin: 8px 0;
}

.hero p {
  max-width: 760px;
}

.eyebrow {
  color: #8fd7c7;
  font-size: 14px;
  font-weight: 700;
  text-transform: uppercase;
}

.workspace {
  display: grid;
  gap: 20px;
  grid-template-columns: minmax(320px, 1fr) minmax(280px, 0.8fr) minmax(320px, 1fr);
  padding: 24px;
}

.panel {
  background: white;
  border: 1px solid #dfe5e8;
  border-radius: 8px;
  padding: 20px;
}

.panel-header {
  align-items: center;
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.composer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

textarea {
  border: 1px solid #cfd8dc;
  border-radius: 6px;
  min-height: 190px;
  padding: 12px;
  resize: vertical;
}

button {
  background: #176b5d;
  border: 0;
  border-radius: 6px;
  color: white;
  cursor: pointer;
  padding: 10px 14px;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.session-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.session {
  align-items: center;
  background: #f7fafb;
  border: 1px solid #dfe5e8;
  color: #172026;
  display: flex;
  justify-content: space-between;
  text-align: left;
}

.session.selected {
  border-color: #176b5d;
}

.status {
  color: #176b5d;
  font-size: 28px;
  font-weight: 800;
}

.summary {
  background: #eef7f4;
  border-radius: 6px;
  padding: 12px;
}

.error {
  color: #b3261e;
}

@media (max-width: 980px) {
  .workspace {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 4: Run frontend build**

Run:

```bash
cd apps/web
npm install
npm run build
```

Expected: TypeScript and Vite build complete successfully.

- [ ] **Step 5: Commit**

```bash
git add apps/web
git commit -m "feat: add phase zero web console"
```

---

### Task 7: Docker Compose Self-Host Skeleton

**Files:**
- Create: `backend/Dockerfile`
- Create: `apps/web/Dockerfile`
- Create: `docker-compose.yml`

- [ ] **Step 1: Add backend Dockerfile**

Create `backend/Dockerfile`:

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e ".[dev]"

CMD ["uvicorn", "puppyrun_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Add web Dockerfile**

Create `apps/web/Dockerfile`:

```dockerfile
FROM node:22-slim

WORKDIR /app

COPY package.json /app/package.json
RUN npm install

COPY . /app

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
```

- [ ] **Step 3: Add Docker Compose**

Create `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: puppyrun
      POSTGRES_PASSWORD: puppyrun
      POSTGRES_DB: puppyrun
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U puppyrun -d puppyrun"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  api:
    build:
      context: ./backend
    environment:
      PUPPYRUN_ENV: development
      PUPPYRUN_DATABASE_URL: postgresql+asyncpg://puppyrun:puppyrun@postgres:5432/puppyrun
      PUPPYRUN_REDIS_URL: redis://redis:6379/0
      PUPPYRUN_CORS_ORIGINS: '["http://localhost:5173"]'
    command: >
      sh -c "alembic upgrade head &&
             uvicorn puppyrun_api.main:app --host 0.0.0.0 --port 8000"
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker:
    build:
      context: ./backend
    environment:
      PUPPYRUN_ENV: development
      PUPPYRUN_DATABASE_URL: postgresql+asyncpg://puppyrun:puppyrun@postgres:5432/puppyrun
      PUPPYRUN_REDIS_URL: redis://redis:6379/0
    command: arq puppyrun_worker.main.WorkerSettings
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  web:
    build:
      context: ./apps/web
    environment:
      VITE_API_BASE_URL: http://localhost:8000
    ports:
      - "5173:5173"
    depends_on:
      - api
```

- [ ] **Step 4: Run compose smoke test**

Run:

```bash
docker compose up --build
```

In another terminal:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok","service":"puppyrun-api"}
```

- [ ] **Step 5: Verify full dummy path manually**

Open `http://localhost:5173`, create a session, start the dummy Agent run, wait for status to become `completed`.

Expected: the selected session shows `completed` and summary text:

```text
Phase 0 dummy Agent completed. Real research workflow is not enabled yet.
```

- [ ] **Step 6: Commit**

```bash
git add backend/Dockerfile apps/web/Dockerfile docker-compose.yml
git commit -m "chore: add docker compose skeleton"
```

---

### Task 8: CI Workflow And Phase 0 Documentation

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`

- [ ] **Step 1: Add CI workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install --upgrade pip
      - run: python -m pip install -e ".[dev]"
      - run: ruff check .
      - run: pytest -q

  web:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: apps/web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - run: npm install
      - run: npm run build
```

- [ ] **Step 2: Update README with verification commands**

Append to `README.md`:

```markdown
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
```

- [ ] **Step 3: Run local verification**

Run:

```bash
cd backend
. .venv/bin/activate
ruff check .
pytest -q
cd ../apps/web
npm run build
```

Expected:

- Ruff passes.
- Pytest passes.
- Vite build passes.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml README.md
git commit -m "ci: add phase zero verification"
```

---

## Final Phase 0 Verification

After all tasks are complete, run:

```bash
git status --short
docker compose up --build
```

In another terminal:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok","service":"puppyrun-api"}
```

Then open `http://localhost:5173`, create a session, start the dummy Agent run, and confirm the session reaches `completed`.

## Known Phase 0 Limits

- No real LLM calls.
- No real candidate discovery.
- No real evidence collection.
- No auth beyond local development assumptions.
- The repository includes a VPS deployment path under `deploy/vps/`, but it is still a Phase 0 public demo path rather than production hardening.
- Domain DNS and HTTPS are external deployment operations, not application-code changes.
- No MCP adapter is implemented yet.
- The web client refreshes state by polling every two seconds; SSE can replace polling in Phase 1 once the session/event model is stable.

These limits are intentional because Phase 0 proves deployability, process boundaries, and async state flow before Agent-specific research logic is added.

## Plan Self-Review

Spec coverage for Phase 0:

- Frontend shell: covered by Task 6.
- API server: covered by Tasks 2 and 4.
- PostgreSQL: covered by Tasks 3 and 7.
- Redis / queue: covered by Tasks 5 and 7.
- Worker process: covered by Tasks 5 and 7.
- Basic auth or demo user: not implemented in Phase 0 code because the approved Phase 0 success criteria only require session creation and dummy job execution. The UI uses an implicit local user assumption, and explicit auth belongs in the next deploy-facing plan before public access.
- Health check: covered by Tasks 2 and 7.
- Docker Compose: covered by Task 7.
- CI build: covered by Task 8.
- Production environment: represented by Docker Compose, CI, and the follow-up VPS public demo deployment path. Real production hardening remains outside Phase 0.
- Dummy Agent job that updates session state: covered by Task 5.

Self-review results:

- Marker scan: no unresolved markers or vague implementation steps remain.
- Type consistency: Phase 0 models use SQLAlchemy `Uuid` and `JSON` so SQLite unit tests and PostgreSQL runtime can both work.
- Scope control: this plan does not implement real LLM calls, LangGraph/LangChain integration, MCP adapters, candidate discovery, evidence collection, or evals.
