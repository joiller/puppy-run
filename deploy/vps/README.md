# PuppyRun VPS Deployment

This directory runs the PuppyRun Phase 0 public demo on one VPS with Docker Compose.

## Prerequisites

- Docker Engine and Docker Compose are installed on the VPS.
- A DNS record for the public host points to the VPS.
- Inbound TCP ports `80` and `443` are open.
- SSH access to the VPS is available.

## Public Exposure Boundary

Only SSH, HTTP, and HTTPS should be exposed publicly.

PostgreSQL, Redis, the API service, and the worker stay private on the Docker network. Do not publish database, Redis, API, or worker ports to the public internet.

## First Deployment

Run these commands from the repository root on the VPS.

Create the VPS environment file:

```sh
cp deploy/vps/.env.example deploy/vps/.env
```

Do not commit `deploy/vps/.env`; it contains deployment secrets.

Edit `deploy/vps/.env` with the real public host, a strong database password, the URL-encoded database password, and the CORS origin.

`POSTGRES_PASSWORD` is the raw database password passed to PostgreSQL. `POSTGRES_PASSWORD_URLENCODED` must be its URL-encoded form because it is interpolated into `PUPPYRUN_DATABASE_URL` for the API and worker. URL-encode characters such as `@`, `:`, `/`, `%`, and `#`. Generate the encoded value with:

```sh
python3 - <<'PY'
from urllib.parse import quote
print(quote(input("PostgreSQL password: "), safe=""))
PY
```

Put the command output in `POSTGRES_PASSWORD_URLENCODED` and keep the original raw value in `POSTGRES_PASSWORD`.

Example:

```env
PUPPYRUN_PUBLIC_HOST=demo.example.com
POSTGRES_PASSWORD=replace-with-a-strong-password
POSTGRES_PASSWORD_URLENCODED=replace-with-a-strong-password
PUPPYRUN_CORS_ORIGINS=["https://demo.example.com"]
```

### Phase 5 public demo safety

For a public live DeepSeek demo, set the live provider credentials and safety values in `deploy/vps/.env`:

```env
PUPPYRUN_LLM_PROVIDER=deepseek
PUPPYRUN_DEEPSEEK_API_KEY=replace-with-private-deepseek-key
PUPPYRUN_DEMO_SAFETY_ENABLED=true
PUPPYRUN_LIVE_DEMO_ENABLED=true
PUPPYRUN_ADMIN_TOKEN=replace-with-private-admin-token
PUPPYRUN_LIVE_RUN_DAILY_LIMIT=20
PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP=3
PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP=10
PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP=120
PUPPYRUN_CLIENT_IP_HEADER=X-Forwarded-For
```

Before starting the stack, generate a private DeepSeek key and a strong private admin token. Replace `PUPPYRUN_DEEPSEEK_API_KEY=replace-with-private-deepseek-key` and `PUPPYRUN_ADMIN_TOKEN=replace-with-private-admin-token`. Do not start the public demo if `PUPPYRUN_LLM_PROVIDER` is not `deepseek`, if either value is empty, or if either value still uses the checked-in placeholder.

Use this local preflight as a required gate before `docker compose up`. A nonzero exit is a stop condition:

```sh
python3 - <<'PY'
from pathlib import Path

env = {}
for line in Path("deploy/vps/.env").read_text().splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key, value = stripped.split("=", 1)
    env[key] = value.strip()

if env.get("PUPPYRUN_LLM_PROVIDER") != "deepseek":
    raise SystemExit("Set PUPPYRUN_LLM_PROVIDER=deepseek before public live demo startup.")

deepseek_key = env.get("PUPPYRUN_DEEPSEEK_API_KEY", "")
if not deepseek_key or deepseek_key == "replace-with-private-deepseek-key":
    raise SystemExit("Set PUPPYRUN_DEEPSEEK_API_KEY to a private non-placeholder value before startup.")

admin_token = env.get("PUPPYRUN_ADMIN_TOKEN", "")
if not admin_token or admin_token == "replace-with-private-admin-token":
    raise SystemExit("Set PUPPYRUN_ADMIN_TOKEN to a private non-placeholder value before startup.")

print("PUPPYRUN_LLM_PROVIDER, PUPPYRUN_DEEPSEEK_API_KEY, and PUPPYRUN_ADMIN_TOKEN are set for public live demo startup.")
PY
```

Do not commit the real DeepSeek key or admin token. After deployment, open `/admin`, enter the token, confirm the current counts, disable live demo, verify new runs are blocked, then re-enable it.

Start the stack:

```sh
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml up --build -d
```

Check API health through the public HTTPS endpoint:

```sh
export PUPPYRUN_PUBLIC_HOST=demo.example.com
curl "https://${PUPPYRUN_PUBLIC_HOST}/health"
```

Expected response:

```json
{"status":"ok","service":"puppyrun-api"}
```

## Updating Deployment

After pulling new code on the VPS, rebuild and restart the stack:

```sh
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml up --build -d
```

## Logs

API logs:

```sh
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml logs -f api
```

Worker logs:

```sh
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml logs -f worker
```

Web logs:

```sh
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml logs -f web
```

## Stop

Stop the stack:

```sh
docker compose --env-file deploy/vps/.env -f deploy/vps/docker-compose.yml down
```

Do not remove volumes unless the demo data should be reset.

## Public Smoke Test

Use your configured `PUPPYRUN_PUBLIC_HOST` for the public smoke test.

1. Open `https://${PUPPYRUN_PUBLIC_HOST}`.
2. Create a decision session.
3. Click `Run Phase 1 Agent`.
4. Do not click `Refresh`.
5. Wait until the selected session detail panel shows `completed`.
6. Open `https://${PUPPYRUN_PUBLIC_HOST}/health` and confirm the API health JSON.

Phase 0 public URL verification passes only when both the web page and the async worker loop work through the VPS-hosted PostgreSQL and Redis services.
