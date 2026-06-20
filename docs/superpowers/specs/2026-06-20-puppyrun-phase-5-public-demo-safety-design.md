# PuppyRun Phase 5 Public Demo Safety Design

Date: 2026-06-20

## 1. Decision

Phase 5 v1 will build a public live demo safety shell.

The public demo remains no-login so a recruiter or interviewer can open the site and try it directly. The default public path should use live DeepSeek, protected by conservative Redis-backed quotas, rate limits, an admin kill switch, and clear user-facing failure messages.

This phase hardens the deployed demo around the existing PuppyRun workflow. It does not redesign the Agent workflow, add full authentication, or replace the Phase 4 live eval gate.

## 2. Context

PuppyRun has completed the core decision-workbench path through Phase 3 and added a Phase 4 local DeepSeek live regression gate. The next risk is public operation: a no-login live LLM demo can be abused, can consume cost unexpectedly, and can fail unclearly when quotas or provider settings are misconfigured.

The current API is a single public demo surface. Session creation, run creation, version reruns, and workspace reads are available without authentication. Redis, PostgreSQL, API, worker, and Caddy are already present in the VPS deployment path, so Phase 5 v1 can add runtime protection without introducing a separate infrastructure dependency.

## 3. Goals

- Keep the public demo no-login and easy to try.
- Make live DeepSeek the default public demo path.
- Cap live-run cost exposure with a global daily quota and a per-IP daily quota.
- Cap state growth with a per-IP daily session-create quota.
- Protect read-heavy polling endpoints with lightweight rate limits.
- Give the owner a token-protected admin status and live-demo enable/disable control.
- Return stable, user-friendly quota and disabled-state responses.
- Keep the deployment self-hostable through documented environment variables and Docker Compose.

## 4. Non-Goals

Phase 5 v1 will not include:

- full login, RBAC, user accounts, billing, or payment,
- private repository access,
- export jobs,
- a full metrics dashboard or alerting pipeline,
- CI-hosted live evals or provider comparison,
- a large demo seed-data system,
- durable admin audit records,
- changes to accepted debt `AD-001` unless it is explicitly reopened,
- committed real public hosts, raw IPs, SSH targets, tokens, credentials, or secrets.

## 5. Default Policy

Use conservative defaults for the public demo:

- global live runs per day: `20`,
- live runs per IP per day: `3`,
- sessions created per IP per day: `10`,
- read requests per IP per minute: generous enough for normal 2s polling,
- live demo switch: enabled only when configured for the public demo,
- admin API: disabled unless `PUPPYRUN_ADMIN_TOKEN` is set.

Phase 5 v1 configuration contract for the public VPS demo:

```text
PUPPYRUN_DEMO_SAFETY_ENABLED=true
PUPPYRUN_LIVE_DEMO_ENABLED=true
PUPPYRUN_ADMIN_TOKEN=<private value>
PUPPYRUN_LIVE_RUN_DAILY_LIMIT=20
PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP=3
PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP=10
PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP=120
PUPPYRUN_CLIENT_IP_HEADER=X-Forwarded-For
```

`PUPPYRUN_CLIENT_IP_HEADER` should only be honored when the API is deployed behind the trusted Caddy reverse proxy and the API container is not directly exposed to the public internet. Local development and tests may leave it unset and use the direct request client address.

## 6. Architecture

Add a small demo-safety layer around public API actions instead of changing the core recommendation workflow.

Backend responsibilities:

- Extend `puppyrun_api.config.Settings` with Phase 5 policy values.
- Add a focused backend module, likely `puppyrun_api.demo_limits`, to own:
  - client IP extraction,
  - Redis key construction,
  - daily quota counters,
  - minute-window read limits,
  - best-effort counter rollback,
  - live-demo switch state,
  - admin status payloads.
- Wire public protections into:
  - `POST /api/v1/sessions`,
  - `POST /api/v1/sessions/{session_id}/runs`,
  - `POST /api/v1/sessions/{session_id}/versions`,
  - `GET /api/v1/sessions`,
  - `GET /api/v1/sessions/{session_id}/workspace`.
- Add an admin router under a path such as `/api/v1/admin/demo`, protected by `Authorization: Bearer <PUPPYRUN_ADMIN_TOKEN>`.

Redis is the source of truth for counters and switch state. PostgreSQL remains the source of truth for sessions, versions, runs, and workflow output. No database migration is expected for v1.

Frontend responsibilities:

- Map structured `403` and `429` demo-safety responses to clear public messages.
- Keep polling and read-limit failures from overwriting local draft edits.
- Add a minimal admin route or panel with token input, status refresh, and enable/disable controls.
- Do not expose admin controls prominently in the main workbench.

Deployment responsibilities:

- Update `.env.example`, `deploy/vps/.env.example`, `deploy/vps/docker-compose.yml`, README, and VPS runbook.
- Keep real hosts and secret values out of committed docs.

## 7. Gate Semantics

### Session Creation

`POST /api/v1/sessions` identifies the client IP, checks the per-IP daily session-create quota, increments the counter if allowed, and creates the session. If session creation fails because of a server-side error after the counter is incremented, the implementation should best-effort rollback the counter.

Validation errors should not consume quota.

### Initial Run

`POST /api/v1/sessions/{session_id}/runs` identifies the client IP, checks the live-demo switch, checks the global daily live-run quota, checks the per-IP daily live-run quota, increments both counters if allowed, then creates and enqueues the run.

Missing sessions should still return `404` without consuming live-run quota. If enqueue fails before the run is accepted, the implementation should best-effort rollback both counters.

The quota is consumed once a run is accepted for processing. If DeepSeek or another downstream tool later fails, the quota remains consumed. This keeps public-abuse protection simple and predictable.

### Version Rerun

`POST /api/v1/sessions/{session_id}/versions` uses the same live-run gate as the initial run because targeted reruns can also invoke live DeepSeek. Existing conflict behavior should run before live-run quota is consumed, so `404` and `409` responses do not use a live-run slot.

### Read Endpoints

Read endpoints use a fixed minute-window Redis rate limit per IP. The limit should be generous enough for the current 2s polling loop during a normal manual demo. When exceeded, the API returns `429` with a stable response shape.

### Admin Disable Switch

When the live demo is disabled, new initial runs and version reruns return `403` with a code such as `live_demo_disabled`. Existing sessions remain readable. Session creation may remain allowed unless operational experience shows that it should also be blocked when the demo is disabled.

## 8. Response Contract

Quota and disabled responses should be stable and machine-readable:

```json
{
  "code": "live_run_daily_limit_exceeded",
  "message": "The public live demo has reached today's run limit. Please try again after the reset.",
  "limit": 20,
  "remaining": 0,
  "reset_at": "2026-06-21T00:00:00Z"
}
```

Expected codes include:

- `session_create_daily_limit_exceeded`,
- `live_run_daily_limit_exceeded`,
- `live_run_ip_daily_limit_exceeded`,
- `read_rate_limit_exceeded`,
- `live_demo_disabled`,
- `admin_token_required`,
- `admin_token_invalid`.

The frontend should display the friendly `message` and avoid exposing raw exception text.

## 9. Admin Surface

The admin API should provide the smallest useful control surface:

- `GET /api/v1/admin/demo/status`
  - live demo enabled or disabled,
  - configured limits,
  - global live-run count for the current day,
  - optional current caller IP counts,
  - reset timestamps.
- `POST /api/v1/admin/demo/disable`
  - disables new live runs.
- `POST /api/v1/admin/demo/enable`
  - re-enables new live runs.

All admin endpoints require a bearer token. Responses must not echo `PUPPYRUN_ADMIN_TOKEN` or other secrets.

The admin UI can be minimal:

- route such as `/admin`,
- token input stored only in browser memory or local storage,
- status refresh button,
- enable and disable buttons,
- visible error messages for invalid token and network failures.

## 10. Testing Strategy

Backend tests should cover:

- settings defaults and environment overrides,
- quota key naming and daily reset behavior,
- per-IP session-create quota,
- global live-run quota,
- per-IP live-run quota,
- disabled live demo behavior,
- `404` and `409` paths that must not consume live-run quota,
- enqueue failure rollback where practical,
- read endpoint rate limiting,
- admin token rejection and acceptance,
- admin status and enable/disable behavior,
- secret non-disclosure in admin responses.

Frontend tests should cover:

- friendly public UI messages for quota and disabled responses,
- admin status fetch and enable/disable controls,
- invalid token feedback,
- polling/read failures that do not clear local draft edits.

Verification commands:

```bash
cd backend && .venv/bin/ruff check .
cd backend && .venv/bin/pytest -q
cd apps/web && npm test -- --run
cd apps/web && npm run build
git diff --check
```

Docker and deployment checks:

```bash
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml config
docker compose up --build -d
curl http://localhost:8000/health
docker compose ps
```

The Phase 4 DeepSeek live eval remains a separate release gate and should not be replaced by Phase 5 safety tests:

```bash
cd backend
PUPPYRUN_LLM_PROVIDER=deepseek \
PUPPYRUN_DEEPSEEK_API_KEY=<private value> \
.venv/bin/python -m puppyrun_eval run --suite phase4-live
```

## 11. Manual Acceptance

Manual Phase 5 acceptance should verify:

1. Public demo opens with no login.
2. A user under quota can create a session and start a live run.
3. The normal workbench completion path remains usable.
4. Exceeding the per-IP live-run quota shows a clear public message.
5. Exceeding the session-create quota shows a clear public message.
6. Admin status shows configured limits and current counts.
7. Admin disable blocks new live runs while existing sessions remain readable.
8. Admin enable allows new live runs again when quotas permit.
9. Read-limit errors do not erase local draft edits.
10. Public docs explain how to configure the demo without exposing secrets.

## 12. Implementation Task Cut

The implementation plan should split Phase 5 v1 into these tasks:

1. Configuration contract.
2. Redis demo-limit core.
3. Public API protection.
4. Admin API.
5. Frontend public error handling.
6. Minimal admin UI.
7. Deployment, docs, and release verification.

Each task should stay scoped and independently reviewable. Do not add full login, RBAC, billing, export jobs, a metrics dashboard, or unrelated clarification-parser fixes during Phase 5 v1.

## 13. Future Extensions

Later Phase 5 work can add:

- durable admin audit events,
- richer metrics and alerting,
- admin top-offender views,
- optional shared demo passcode,
- hosted eval report summaries,
- demo seed data management,
- export jobs,
- real user auth and RBAC if the product moves beyond a public demo.
