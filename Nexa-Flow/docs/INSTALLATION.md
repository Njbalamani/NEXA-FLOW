# Installation and deployment guide

## Prerequisites

- Python 3.12+
- PostgreSQL 16+ (or SQLite for a local quick start)
- Redis 7+ (Celery broker/result backend)
- Node.js is **not** required; React, GSAP, Chart.js and Three.js are loaded from CDNs in the browser.

## Local development

PowerShell:

```powershell
cd C:\Users\bmani\OneDrive\Desktop\NexaFlow
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

For an in-process automation engine without Redis, set this in `.env`:

```env
CELERY_TASK_ALWAYS_EAGER=true
```

For real background execution, leave eager mode off and run two extra terminals:

```powershell
celery -A nexaflow worker -l info
celery -A nexaflow beat -l info
```

Celery beat calls `apps.workflows.tasks.run_due_workflows` every minute. You can also trigger it manually:

```powershell
python manage.py run_scheduler
```

Audit logs can be pruned on a schedule. Preview first with `--dry-run`, then delete entries older than the retention window:

```powershell
python manage.py cleanup_audit_logs --days 90 --dry-run
python manage.py cleanup_audit_logs --days 90
```

Open `http://127.0.0.1:8000/`, register an account, and your organization, workspace and free subscription are created automatically.

## PostgreSQL

Set `DATABASE_URL` in `.env`:

```env
DATABASE_URL=postgresql://nexaflow:nexaflow@localhost:5432/nexaflow
```

Create the database once:

```powershell
psql -U postgres -c "CREATE USER nexaflow WITH PASSWORD 'nexaflow';"
psql -U postgres -c "CREATE DATABASE nexaflow OWNER nexaflow;"
python manage.py migrate
```

## Docker Compose

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Services:

| Service | Purpose |
| --- | --- |
| `web` | Django + gunicorn on port 8000 |
| `db` | PostgreSQL 16 |
| `redis` | Celery broker and results |
| `celery_worker` | Executes queued workflow runs |
| `celery_beat` | Enqueues scheduled triggers |

Create an admin user:

```powershell
docker compose exec web python manage.py createsuperuser
```

Stop with `docker compose down`. Volumes preserve database, static files and uploads.

## Tests

```powershell
python manage.py test
python manage.py check
python manage.py makemigrations --check --dry-run
```

The suite covers authentication, workflow creation, validation, execution, conditional routing, scheduled execution, webhook signatures, tasks, payment testing, permissions, analytics and page rendering.

## Production deployment checklist

1. Set `DEBUG=false` and a long random `DJANGO_SECRET_KEY`.
2. Set `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` to real domains.
3. Use managed PostgreSQL and Redis; never reuse development credentials.
4. Set `NEXAFLOW_ENCRYPTION_KEY` before saving integration credentials. Changing it later makes existing encrypted credentials unreadable.
5. Configure SMTP through `EMAIL_*` variables.
6. Run `python manage.py collectstatic` and serve `staticfiles/` through nginx or a CDN.
7. Terminate TLS at the load balancer; the app enables HSTS, secure cookies and proxy SSL headers when `DEBUG=false`, and sends `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, COOP, CORP and cross-domain-policy headers on every response.
8. Run gunicorn behind a reverse proxy and run Celery worker plus beat as separate processes.
9. Set `AI_API_KEY` only if you want provider-backed AI features; otherwise the local analyzer works offline.
10. Back up PostgreSQL and monitor `WorkflowExecution`, `AuditLog` and Celery queue depth.
11. Restrict outbound HTTP from workers for actions; `is_safe_outbound_url` blocks private and metadata hosts, but network-level egress rules are recommended.

Example gunicorn command:

```bash
gunicorn nexaflow.wsgi:application --bind 0.0.0.0:8000 --workers 4 --timeout 120
```

## Environment variables

See `.env.example` for the complete list. Important variables:

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Django signing key |
| `DATABASE_URL` | PostgreSQL or SQLite connection string |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis endpoints |
| `CELERY_TASK_ALWAYS_EAGER` | Run tasks in-process for local development |
| `NEXAFLOW_ENCRYPTION_KEY` | Fernet key source for integration credentials |
| `AI_API_KEY` | Optional OpenAI-compatible API key |
| `EMAIL_*` | SMTP delivery |
| `MAX_NODES_PER_WORKFLOW` | Graph size guard |
| `MAX_EXECUTION_SECONDS` | Execution timeout guard |
| `WEBHOOK_MAX_BODY_BYTES` | Webhook payload size guard |
| `WEBHOOK_REPLAY_TTL_SECONDS` | Duplicate webhook delivery cache window |
| `WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS` | Maximum webhook timestamp age |
| `WEBHOOK_RATE_LIMIT_PER_MINUTE` | Per-IP webhook request limit |
| `TRUST_X_FORWARDED_FOR` | Set `true` only behind a proxy that overwrites `X-Forwarded-For` |
| `ALLOWED_ENV_HEADERS` | Comma-separated allowlist of env vars workflows may read |
| `CACHE_URL` | Redis cache endpoint for shared webhook replay protection |
| `RATE_LIMIT_*` | DRF throttle scopes |

## Troubleshooting

- **Static assets missing:** run `collectstatic` and verify `STATIC_URL` routing.
- **Celery tasks stay queued:** confirm Redis is reachable and a worker is running.
- **Webhook returns 401:** sign the exact raw bytes, not a re-serialized JSON object.
- **AI widget shows local source:** `AI_API_KEY` is not set; this is expected and fully functional.
- **Payment card declined:** test cards ending in `0002` intentionally fail.
- **Credentials unreadable after deploy:** `NEXAFLOW_ENCRYPTION_KEY` changed; re-enter credentials through the API.
