# NexaFlow database ER diagram

```mermaid
erDiagram
  USER ||--o{ ORGANIZATION_MEMBER : joins
  USER ||--o{ ORGANIZATION : owns
  ORGANIZATION ||--o{ ORGANIZATION_MEMBER : has
  ORGANIZATION ||--o{ WORKSPACE : contains
  ORGANIZATION ||--o{ TEAM : contains
  WORKSPACE ||--o{ TEAM : scopes
  TEAM ||--o{ TEAM_MEMBERSHIP : has
  USER ||--o{ TEAM_MEMBERSHIP : belongs_to
  ORGANIZATION ||--o{ WORKFLOW : owns
  WORKSPACE ||--o{ WORKFLOW : groups
  WORKFLOW ||--o{ WORKFLOW_NODE : contains
  WORKFLOW ||--o{ WORKFLOW_CONNECTION : contains
  WORKFLOW_NODE ||--o{ WORKFLOW_CONNECTION : source
  WORKFLOW_NODE ||--o{ WORKFLOW_CONNECTION : target
  WORKFLOW ||--o{ WORKFLOW_TRIGGER : exposes
  WORKFLOW ||--o{ WORKFLOW_EXECUTION : records
  WORKFLOW_TRIGGER ||--o{ WORKFLOW_EXECUTION : starts
  WORKFLOW_EXECUTION ||--o{ EXECUTION_STEP : traces
  WORKFLOW_NODE ||--o{ EXECUTION_STEP : executed_as
  ORGANIZATION ||--o{ TASK : owns
  WORKFLOW ||--o{ TASK : creates
  WORKFLOW_EXECUTION ||--o{ TASK : creates
  USER ||--o{ TASK : assigned
  TASK ||--o{ TASK_EVENT : logs
  ORGANIZATION ||--o{ INTEGRATION : configures
  INTEGRATION ||--o{ WEBHOOK_DELIVERY : logs
  ORGANIZATION ||--o{ NOTIFICATION : emits
  USER ||--o{ NOTIFICATION : receives
  ORGANIZATION ||--|| SUBSCRIPTION : has
  SUBSCRIPTION ||--o{ PAYMENT : bills
  ORGANIZATION ||--o{ AUDIT_LOG : records
  USER ||--o{ AUDIT_LOG : acts

  USER {
    bigint id PK
    string email UK
    string full_name
    string timezone
    bigint active_organization FK
  }
  ORGANIZATION {
    bigint id PK
    string name
    string slug UK
    bigint owner FK
  }
  ORGANIZATION_MEMBER {
    bigint id PK
    bigint organization FK
    bigint user FK
    string role
    boolean is_active
  }
  WORKSPACE {
    bigint id PK
    bigint organization FK
    string name
    string slug
  }
  TEAM {
    bigint id PK
    bigint organization FK
    bigint workspace FK
    string name
  }
  TEAM_MEMBERSHIP {
    bigint id PK
    bigint team FK
    bigint user FK
    string role
  }
  WORKFLOW {
    bigint id PK
    bigint organization FK
    bigint workspace FK
    string name
    string status
    json definition
    string webhook_token UK
  }
  WORKFLOW_NODE {
    bigint id PK
    bigint workflow FK
    string key
    string type
    json config
    int position_x
    int position_y
  }
  WORKFLOW_CONNECTION {
    bigint id PK
    bigint workflow FK
    bigint source FK
    bigint target FK
    string source_handle
  }
  WORKFLOW_TRIGGER {
    bigint id PK
    bigint workflow FK
    string type
    json config
    string webhook_secret
    boolean is_active
  }
  WORKFLOW_EXECUTION {
    bigint id PK
    bigint workflow FK
    bigint trigger FK
    string status
    json input
    json context
    json result
    int duration_ms
  }
  EXECUTION_STEP {
    bigint id PK
    bigint execution FK
    bigint node FK
    string node_key
    string status
    json input
    json output
  }
  TASK {
    bigint id PK
    bigint organization FK
    bigint workflow FK
    bigint execution FK
    bigint assignee FK
    string status
    string priority
    datetime due_at
  }
  TASK_EVENT {
    bigint id PK
    bigint task FK
    bigint actor FK
    string event
    json data
  }
  INTEGRATION {
    bigint id PK
    bigint organization FK
    string name
    string provider
    text credentials_encrypted
    string credential_ref
  }
  WEBHOOK_DELIVERY {
    bigint id PK
    bigint organization FK
    bigint integration FK
    string url
    int status_code
    boolean success
  }
  NOTIFICATION {
    bigint id PK
    bigint organization FK
    bigint user FK
    string title
    string category
    datetime read_at
  }
  SUBSCRIPTION {
    bigint id PK
    bigint organization FK
    string plan
    string status
    int seats
    datetime current_period_end
  }
  PAYMENT {
    bigint id PK
    bigint organization FK
    bigint subscription FK
    string provider_reference UK
    decimal amount
    string status
  }
  AUDIT_LOG {
    bigint id PK
    bigint organization FK
    bigint actor FK
    string action
    string target_type
    json metadata
  }
```

All timestamps use UTC. Unique constraints include `(organization, slug)` for workflows and workspaces, `(workflow, key)` for nodes, `(workflow, source, target, source_handle)` for connections, and `(organization, user)` for memberships.
