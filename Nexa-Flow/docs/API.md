# NexaFlow REST API v1

Base URL: `/api/v1/`

Every response includes an `X-Request-ID` header. Send the same header on a request to preserve a correlation ID across logs and clients.

Interactive documentation (when `drf-spectacular` is installed):

- Swagger UI: `/api/docs/`
- ReDoc: `/api/redoc/`
- OpenAPI schema: `/api/schema/`

## Authentication

- Session authentication for browser pages.
- Token authentication for API clients.
- Obtain a token with `POST /auth/login/` or during `POST /auth/register/`.
- Send it as `Authorization: Token <token>`.

Multi-organization users should send `X-Organization-ID: <id>` on every request. If omitted, the user's active organization is used.

## Rate limits

| Scope | Default | Applies to |
| --- | --- | --- |
| `auth` | 10/min | register and login |
| `api` | 120/min | standard endpoints and analytics |
| `webhook` | 300/min | incoming webhook endpoints |
| `ai` | 20/min | AI assistant endpoints |
| `billing` | 20/min | test payment endpoint |

List endpoints use page-number pagination with `page` and `page_size` (maximum 200).

Errors have this shape:

```json
{"error": true, "code": 400, "detail": {"field": ["message"]}}
```

## Endpoints

### Authentication

| Method | Path | Description |
| --- | --- | --- |
| POST | `/auth/register/` | Create user, default organization, workspace, free subscription and token |
| POST | `/auth/login/` | Exchange email/password for token |
| POST | `/auth/logout/` | Delete the current token |
| GET/PATCH | `/auth/me/` | Read or update current user |

### Organizations and access

| Method | Path | Description |
| --- | --- | --- |
| GET/POST | `/organizations/` | List accessible organizations / create one |
| GET/PATCH/DELETE | `/organizations/{id}/` | Manage organization |
| GET/POST | `/members/` | List/invite members |
| GET/PATCH/DELETE | `/members/{id}/` | Manage membership and role |
| GET/POST | `/workspaces/` | List/create workspaces |
| GET/PATCH/DELETE | `/workspaces/{id}/` | Manage workspace |
| GET/POST | `/teams/` | List/create teams |
| GET/PATCH/DELETE | `/teams/{id}/` | Manage team |

Roles: `viewer`, `member`, `manager`, `admin`, `owner`. Destructive workflow, workspace, team and task operations require `manager` or higher. Member and integration credential operations require `admin` or higher.

List endpoints support `?search=`, `?ordering=`, `?page=` and `?page_size=`. Searchable fields include workflow/task names and descriptions, execution status and error, integration names, notification content, and webhook delivery events.

### Workflows

| Method | Path | Description |
| --- | --- | --- |
| GET/POST | `/workflows/` | List or create workflows; filter templates with `?is_template=true` |
| GET/PATCH/DELETE | `/workflows/{id}/` | Manage workflow |
| GET | `/workflows/{id}/export/` | Export a portable definition without database IDs |
| POST | `/workflows/import/` | Validate and import a definition as a new workflow |
| GET | `/workflows/templates/` | List reusable workflow templates |
| POST | `/workflows/{id}/use-template/` | Instantiate a template as a draft workflow, optionally with `name` |
| GET/PUT | `/workflows/{id}/definition/` | Read or atomically save nodes, connections and triggers |
| GET | `/workflows/{id}/validate/` | Validate the saved graph |
| POST | `/workflows/{id}/validate-draft/` | Validate a definition payload without saving |
| POST | `/workflows/{id}/execute/` | Queue a run with optional JSON `input` |
| POST | `/workflows/{id}/duplicate/` | Clone workflow as draft |
| GET | `/workflows/{id}/executions/` | Executions for one workflow |
| GET/POST | `/nodes/`, `/connections/`, `/triggers/` | Granular graph records |
| GET | `/executions/` | All executions |
| GET | `/executions/{id}/steps/` | Step-by-step execution trace |
| POST | `/executions/{id}/cancel/` | Revoke and mark a queued or running execution cancelled |
| POST | `/executions/{id}/retry/` | Re-run a failed, cancelled or timed out execution |
| GET | `/workflow-versions/?workflow={id}` | List immutable definition snapshots, newest first |
| POST | `/workflow-versions/{id}/restore/` | Restore a snapshot as a new workflow version |

Definition payload:

```json
{
  "definition": {
    "name": "Approval automation",
    "description": "Routes high-value requests",
    "status": "active",
    "nodes": [
      {"key": "trigger_1", "type": "trigger", "name": "Start", "config": {}, "position": {"x": 60, "y": 160}},
      {"key": "condition_1", "type": "condition", "name": "Amount check", "config": {"field": "trigger.payload.amount", "operator": "gte", "value": 100}, "position": {"x": 260, "y": 160}},
      {"key": "action_1", "type": "action", "name": "Create task", "config": {"action": "create_task", "title": "Review {{trigger.payload.amount}}", "priority": "high"}, "position": {"x": 480, "y": 160}}
    ],
    "connections": [
      {"source": "trigger_1", "target": "condition_1", "source_handle": "out"},
      {"source": "condition_1", "target": "action_1", "source_handle": "true"}
    ],
    "triggers": [
      {"name": "Manual", "type": "manual", "config": {}, "is_active": true}
    ]
  }
}
```

Node types: `trigger`, `condition`, `action`, `delay`, `transform`, `webhook`.

Action types: `create_task`, `send_email`, `http_request`, `webhook`, `slack`, `notification`, `log`, `ai`.

Condition operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `not_contains`, `in`, `not_in`, `exists`, `missing`, `empty`, `not_empty`. A restricted `expression` string is also supported; attribute/name lookups resolve from the execution context.

Template placeholders use `{{ trigger.payload.field }}`, `{{ vars.name }}` and `{{ steps.node_key.output }}`.

Seed a starter template gallery into an organization with `python manage.py seed_templates --organization <id>` or `python manage.py seed_templates --user email@example.com`. Templates are available through `/workflows/templates/` and can be instantiated with `/workflows/{id}/use-template/`.

Every definition save creates an immutable `WorkflowVersion` snapshot. Restoring a snapshot saves its nodes, connections, triggers, name and status as a new version, so the previous state remains recoverable.

### Webhook execution

Webhook endpoints live outside the versioned API:

`POST /api/webhooks/{workflow.webhook_token}/`

Sign the **exact raw request body** with the trigger's `webhook_secret`. For replay protection, also send a Unix timestamp and nonce, and include `{timestamp}.` before the body in the signed payload:

```python
import hashlib, hmac, json, time, requests
body = json.dumps({"amount": 250})
timestamp = str(int(time.time()))
signed = timestamp.encode() + b"." + body.encode()
signature = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
requests.post(url, data=body, headers={
    "Content-Type": "application/json",
    "X-NexaFlow-Signature": signature,
    "X-NexaFlow-Timestamp": timestamp,
    "X-NexaFlow-Nonce": "unique-delivery-id",
})
```

Legacy raw-body signatures remain accepted, but all deliveries are deduplicated by signature for `WEBHOOK_REPLAY_TTL_SECONDS`. `X-Hub-Signature-256` is also accepted. Invalid or stale signatures return `401`, duplicate deliveries return `409`, inactive endpoints return `404`, oversized bodies return `413`, and plan-limit breaches return `429`. Success returns `202` with `execution_id`, `status`, and `execution_url`. Rate limiting uses `REMOTE_ADDR` by default;
 enable `TRUST_X_FORWARDED_FOR` only when a trusted proxy overwrites that header. Set `CACHE_URL` to Redis in multi-worker deployments so replay protection is shared.

### Tasks and notifications

| Method | Path | Description |
| --- | --- | --- |
| GET/POST | `/tasks/` | List/create tasks; filter with `?status=pending` |
| GET/PATCH/DELETE | `/tasks/{id}/` | Manage task |
| POST | `/tasks/{id}/status/` | Set status: pending, in_progress, completed, cancelled |
| POST | `/tasks/bulk-status/` | Update up to 100 tasks with `{"ids": [...], "status": "..."}` |
| GET | `/tasks/{id}/events/` | Task history |
| GET | `/notifications/` | Inbox for the current user and organization-wide notices |
| POST | `/notifications/{id}/mark-read/` | Mark one notification read |
| POST | `/notifications/mark-all-read/` | Mark every unread notification in the inbox read |

### Integrations

| Method | Path | Description |
| --- | --- | --- |
| GET/POST | `/integrations/` | List/create integrations |
| GET/PATCH/DELETE | `/integrations/{id}/` | Manage integration |
| POST | `/integrations/{id}/test/` | Send a safe test delivery or test email |
| GET | `/integrations/{id}/credentials-status/` | Report whether credentials exist, without values |
| GET | `/webhook-deliveries/?integration={id}` | List outbound delivery attempts |
| POST | `/webhook-deliveries/{id}/retry/` | Re-send a stored delivery as a new log entry |

`credentials` is write-only. The API response contains `has_credentials` and `credential_fields` (names only), never credential values or the encrypted blob. Prefer `credential_ref` pointing to an environment variable.

### Billing and analytics

| Method | Path | Description |
| --- | --- | --- |
| GET | `/subscriptions/` | Current plan, limits and real usage |
| GET | `/payments/` | Billing history |
| POST | `/billing/test-payment/` | Test gateway charge |
| GET | `/analytics/` | Real database metrics and chart series |

Test cards: any 12+ digit number succeeds except numbers ending in `0002`, which are declined. Only the last four digits are stored.

```json
{"plan": "pro", "card_number": "4242424242424242"}
```

### AI assistant

| Method | Path | Description |
| --- | --- | --- |
| POST | `/ai/generate/` | Convert natural language into a validated workflow graph; `save: true` stores it as draft |
| POST | `/ai/explain-failure/` | Explain an execution failure using stored steps |
| GET | `/ai/recommendations/` | Find automation opportunities from real usage |
| GET | `/ai/improvements/{workflow_id}/` | Suggest structural improvements |

When `AI_API_KEY` is absent, the local deterministic analyzer is used. Every AI-generated graph is passed through the validator before being returned or saved.

### Audit and system

| Method | Path | Description |
| --- | --- | --- |
| GET | `/audit-logs/` | Filterable audit trail |
| GET | `/` | API metadata |
| GET | `/health/` | Process, database and cache health plus Celery mode (outside `/api/v1/`) |

## WebSocket events

Connect to `/ws/workflows/{workflow_id}/` with an authenticated session. The server sends:

```json
{"type": "workflow.event", "event_type": "execution.updated", "execution_id": 42, "status": "success"}
```
