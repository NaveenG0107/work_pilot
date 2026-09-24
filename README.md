# WorkPilot — Backend API

WorkPilot is a modern, high-performance, asynchronous REST API powering the WorkPilot agile project management and issue-tracking platform (similar to Jira and Linear). Built with **FastAPI**, **SQLAlchemy 2.0 (Async)**, **PostgreSQL**, **Redis**, and **Supabase S3**, it delivers enterprise-grade multi-tenancy, granular RBAC, sprint planning, task & user story tracking, audit logging, and real-time collaboration.

---

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Architecture](#project-architecture)
- [Environment Configuration](#environment-configuration)
- [Quick Start](#quick-start)
  - [Option A: Running with Docker Compose (Recommended)](#option-a-running-with-docker-compose-recommended)
  - [Option B: Running Locally with Poetry](#option-b-running-locally-with-poetry)
- [Database Migrations & Seeding](#database-migrations--seeding)
  - [Automated Migrations](#automated-migrations)
  - [Database Seeder CLI](#database-seeder-cli)
  - [Default Credentials](#default-credentials)
- [API Modules & Route Overview](#api-modules--route-overview)
- [API Design & Error Handling Conventions](#api-design--error-handling-conventions)
- [Testing](#testing)

---

## Features

### 🏢 Multi-Tenancy & Organization Management
- Isolated organization workspaces with custom branding and logos.
- Organization member invitations with tokenized email delivery (via **Brevo** / **Resend**).
- Role-Based Access Control (**RBAC**) with customizable roles and granular resource permissions (`projects`, `sprints`, `tasks`, `user_stories`, `comments`).

### 🚀 Agile Project & Sprint Planning
- Project management with unique project keys (e.g. `PROJ`).
- Sprint lifecycle management (create, start, complete, and track active sprint backlogs).
- User stories and backlog management with status workflow tracking.
- Dual-lookup support: Fetch tasks and stories interchangeably by either internal UUID or human-readable key (e.g. `PROJ-101`).

### 📋 Task & Work Item Tracking
- Tasks and subtasks with priority, due dates, assignee, estimation, and custom statuses.
- Multi-attachment uploads stored securely via **Supabase S3** storage.
- Real-time comment threads with user mentions and activity history.
- Tagging and labels across all work items.
- Favorites / Bookmarks for rapid navigation across projects and issues.

### 📊 Dashboards, Analytics & Auditing
- Aggregated organization and project dashboard statistics (sprint velocity, task completion rate, workload distribution).
- Enterprise audit trail (`/api/v1/audit`) capturing critical system events, membership changes, and issue mutations.
- High-performance full-text and scoped search across tasks, user stories, and projects.

### 🛡️ Security & Performance
- **Argon2** password hashing via `pwdlib`.
- **JWT** Authentication (HS256) with sliding session middleware for seamless token renewal.
- Organization-scoped security interceptors preventing accidental auto-logouts on client devices.
- **Redis** caching layer with automated cache invalidation middleware (`ProjectCacheInvalidationMiddleware`).

---

## Tech Stack

| Category | Technology |
|---|---|
| **Framework** | [FastAPI](https://fastapi.tiangolo.com/) (Python 3.12+) |
| **ASGI Server** | [Uvicorn](https://www.uvicorn.org/) |
| **ORM & Database** | [SQLAlchemy 2.0 (Async)](https://www.sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/) |
| **Database** | [PostgreSQL 15+](https://www.postgresql.org/) (driver: `psycopg 3` async / binary) |
| **Caching** | [Redis 7](https://redis.io/) (Alpine) |
| **Object Storage** | [Supabase S3](https://supabase.com/docs/guides/storage) / AWS S3 compatible (`boto3`) |
| **Email Providers** | [Brevo](https://www.brevo.com/) (primary), [Resend](https://resend.com/) (fallback) |
| **Authentication** | [PyJWT](https://pyjwt.readthedocs.io/), [pwdlib (Argon2)](https://github.com/frankie567/pwdlib) |
| **Validation** | [Pydantic v2](https://docs.pydantic.dev/) + `pydantic-settings` |
| **ID Generator** | [UUIDv7](https://github.com/oittaa/uuid6-python) (time-sortable UUIDs) |
| **Testing** | [pytest](https://docs.pytest.org/), [HTTPX](https://www.python-httpx.org/) |
| **Package Manager**| [Poetry](https://python-poetry.org/) |

---

## Project Architecture

```
work_pilot/
├── alembic/                       # Database schema migrations
│   ├── env.py
│   └── versions/
├── src/
│   ├── audit/                     # Audit trail logging & endpoints
│   ├── auth/                      # User authentication, registration & profile
│   ├── comments/                  # Task & story comment threads
│   ├── custom_status/             # Custom workflow statuses
│   ├── dashboard/                 # Analytics & dashboard metrics
│   ├── favorite/                  # Project & task bookmarks/favorites
│   ├── integrations/              # External service integrations
│   ├── label/                     # Label & tagging system
│   ├── organization/              # Multi-tenant organizations, roles & permissions
│   ├── project/                   # Project CRUD, members & project keys
│   ├── public/                    # Public endpoints (countries, invitation verify)
│   ├── search/                    # Global & scoped search
│   ├── serial/                    # Serial key generation for issue trackers
│   ├── sprint/                    # Sprint management & agile boards
│   ├── task/                      # Tasks, subtasks & attachment handling
│   ├── user_story/                # User stories & backlog management
│   ├── user_story_status/         # User story workflow states
│   ├── work_item/                 # Unified work item aggregation
│   ├── utils/                     # Helpers (storage, email, middleware, migration)
│   │   ├── cache_invalidation_middleware.py
│   │   ├── database_migration.py  # Auto-migration runner on startup
│   │   ├── session_middleware.py   # Sliding session middleware
│   │   └── storage.py             # S3 object storage client
│   ├── config.py                  # Environment config & structured logger
│   ├── database.py                # Async engine & sessionmaker
│   ├── main.py                    # FastAPI application initialization & routes
│   ├── response.py                # Standardized JSON response envelope
│   └── seeder.py                  # Database seeder (permissions, countries, super admin)
├── tests/
│   └── test_storage.py            # Storage & utility unit tests
├── docker-compose.yaml            # Docker composition (FastAPI + Redis)
├── dockerfile                     # Multi-stage production container
├── docker-entrypoint.sh           # Container entrypoint (migration + server startup)
├── pyproject.toml                 # Poetry dependencies & project configuration
└── .env.example                   # Environment variable template
```

---

## Environment Configuration

Create a `.env` file from the provided `.env.example`:

```bash
cp .env.example .env
```

### Essential Environment Variables

```env
# Database (PostgreSQL)
DATABASE_URL=postgresql+psycopg://postgres:password@localhost:5432/db_name

# Startup Migrations
AUTO_MIGRATE=true
ALLOW_LEGACY_DB_STAMP=false
DB_WAIT_TIMEOUT_SECONDS=60
MIGRATION_LOCK_TIMEOUT_SECONDS=120

# JWT & Authentication
JWT_SECRET_KEY=your-super-secret-key-change-this-in-production
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
JWT_SLIDING_SESSION_ENABLED=true
JWT_RENEWAL_INTERVAL=60

# Redis Cache
REDIS_HOST=localhost
REDIS_PORT=6379
PERFORMANCE_CACHE_TTL_SECONDS=30
PERFORMANCE_CACHE_TIMEOUT_SECONDS=0.25

# CORS Settings
CORS_ORIGINS=*

# S3 / Supabase Storage (Task & Comment Attachments)
S3_ENDPOINT=https://your-project-id.storage.supabase.co/storage/v1/s3
S3_PUBLIC_ENDPOINT=https://your-project-id.supabase.co/storage/v1/object/public
S3_REGION=ap-south-1
S3_ACCESS_KEY_ID=your_s3_access_key_id
S3_SECRET_ACCESS_KEY=your_s3_secret_access_key
S3_BUCKET=work_pilot_bucket
S3_MAX_FILE_FILE_MB=5
ATTACHMENT_MAX_FILE_SIZE_MB=10
ATTACHMENT_MAX_FILES_COUNT=5

# Transactional Email (Invitations & Password Resets)
BREVO_API_KEY=your-brevo-api-key
BREVO_FROM_EMAIL=no-reply@workpilot.com
RESEND_API_KEY=your-resend-api-key
RESEND_FROM_EMAIL=no-reply@workpilot.com
```

---

## Quick Start

### Option A: Running with Docker Compose (Recommended)

Docker Compose automatically spins up the **FastAPI** application and a **Redis** container. The container entrypoint automatically validates the PostgreSQL connection, runs pending database migrations, and boots the Uvicorn server:

```bash
docker compose up --build
```

The API will be accessible at:
- **API URL**: `http://localhost:8000`
- **Swagger Documentation**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **Health Check**: `http://localhost:8000/health_check`

---

### Option B: Running Locally with Poetry

#### 1. Install Dependencies
```bash
poetry install --with test
```

#### 2. Run Database Migrations
```bash
poetry run alembic upgrade head
```

#### 3. Seed Default Permissions and Super Admin
```bash
poetry run python -m src.seeder
```

#### 4. Start the Development Server
```bash
poetry run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Database Migrations & Seeding

### Automated Migrations
On startup, `src/utils/database_migration.py` safely acquires a PostgreSQL advisory lock, checks for pending Alembic revisions, and runs `alembic upgrade head` before serving HTTP traffic.

To run migrations manually:
```bash
poetry run alembic upgrade head
```

### Database Seeder CLI
The repository includes an idempotent database seeder (`src/seeder.py`) to populate initial system data.

#### 1. Seed All Data
To seed everything at once (Default Permissions + ISO Countries + Super Admin user & role):

```bash
# Using Poetry
poetry run python -m src.seeder

# Using Docker
docker compose exec fastapi python -m src.seeder
```

#### 2. Seed Specific Data Only
If you only need to seed specific components without modifying the rest:

```bash
# Seed only system permissions (projects, sprints, tasks, user stories, comments)
poetry run python -m src.seeder --permissions

# Seed only ISO countries list
poetry run python -m src.seeder --countries

# Seed only super admin user and system role
poetry run python -m src.seeder --super-admin
```

### Default Credentials
When running the seeder, the default system administrator account created is:

| Field | Value |
|---|---|
| **Email** | `demo@yopmail.com` |
| **Username** | `Demos` |
| **Password** | `Demo@123` |

---

## API Modules & Route Overview

All core business endpoints are exposed under `/api/v1`:

| Route Prefix | Module | Description |
|---|---|---|
| `/api/v1/auth` | Authentication | Signup, login, refresh token, logout, profile, password reset, invitations |
| `/api/v1/organizations` | Organizations | Tenant creation, workspace management, roles, and member invitations |
| `/api/v1/projects` | Projects | Project CRUD, project keys/codes, members, and project settings |
| `/api/v1/sprints` | Sprints | Agile sprint cycles, backlog assignment, start/complete sprints |
| `/api/v1/user_stories` | User Stories | User stories, epics, backlog items, story-status transitions |
| `/api/v1/tasks` | Tasks | Tasks, subtasks, assignments, priorities, file attachments |
| `/api/v1/work_items` | Work Items | Unified aggregation across stories, tasks, and bugs |
| `/api/v1/custom_status` | Custom Status | Workflow status transitions per project / organization |
| `/api/v1/comments` | Comments | Item commentary threads and mention tracking |
| `/api/v1/labels` | Labels | Tagging and categorization for work items |
| `/api/v1/favorites` | Favorites | Bookmarked / pinned projects, tasks, and stories |
| `/api/v1/dashboard` | Dashboard | Workspace metrics, sprint velocity, completion statistics |
| `/api/v1/audit` | Audit Trail | Activity history and security audit logging |
| `/api/v1/search` | Search | Global and project-scoped text search |
| `/api/v1/public` | Public | Publicly accessible endpoints (ISO countries, invitation verify) |
| `/health_check` | System | Service liveness probe |

---

## API Design & Error Handling Conventions

WorkPilot enforces strict, predictable response schemas and error-handling patterns across all endpoints.

---

### 1. Standard Success Schema & Examples

#### `200 OK` — Standard Success (Fetch / Update / Query)
Returned for successful read and update operations.

**Response Schema:**
```json
{
  "success": true,
  "status_code": 200,
  "message": "string",
  "data": {
    "additionalProp1": {}
  },
  "meta": {
    "total": 0,
    "page": 0,
    "limit": 0
  }
}
```

#### `201 Created` — Resource Created
Returned when a new entity (task, user story, project, sprint, organization, comment) is successfully created.

**Response Schema:**
```json
{
  "success": true,
  "status_code": 201,
  "message": "string",
  "data": {
    "id": "string",
    "key": "string",
    "additionalProp1": {}
  }
}
```

---

### 2. Status Codes & Error Envelopes

Every error response follows a standard OpenAPI schema:

```json
{
  "success": false,
  "error": {
    "code": "string",
    "status_code": 0,
    "message": "string"
  }
}
```

#### Summary Reference Table

| HTTP Status | Error Code | When Triggered | Client Behavior / Action |
|---|---|---|---|
| **`400`** | `BAD_REQUEST` / `VALIDATION_ERROR` | Malformed payload, invalid format, missing required parameters (`project_id`, `task_id`) | Display field validation errors on UI |
| **`401`** | `UNAUTHORIZED` | Missing, expired, or invalid JWT Bearer token | **Trigger auto-logout** & redirect to Sign In |
| **`403`** | `ORGANIZATION_REQUIRED` | Authenticated user has not selected or created an organization | **DO NOT auto-logout**. Redirect to Organization Setup/Picker |
| **`403`** | `FORBIDDEN` | Authenticated user lacks RBAC permissions for the requested action | Display "Access Denied" permission notice |
| **`404`** | `RESOURCE_NOT_FOUND` | Item, project, task, sprint, or user does not exist or was deleted | Display 404 / Not Found screen |
| **`409`** | `CONFLICT` | Unique constraint violation (duplicate project key, email already exists, sprint overlap) | Alert user to change duplicate value |
| **`500`** | `INTERNAL_SERVER_ERROR` | Unhandled server exception or database connectivity issue | Display generic retry message |

---

### 3. Response Schemas by Status Code

#### `400 Bad Request` (`BAD_REQUEST` / `VALIDATION_ERROR`)
Triggered when request validation fails, required parameters are missing (e.g., `project_id`), or input formats are invalid.

```json
{
  "success": false,
  "error": {
    "code": "BAD_REQUEST",
    "status_code": 400,
    "message": "string"
  }
}
```

Validation error schema (from Pydantic request body/query validation):
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "status_code": 400,
    "message": "string"
  }
}
```

#### `401 Unauthorized` (`UNAUTHORIZED`)
Triggered when the `Authorization` header is missing, malformed, or the JWT access token is expired/revoked.

```json
{
  "success": false,
  "error": {
    "code": "UNAUTHORIZED",
    "status_code": 401,
    "message": "string"
  }
}
```
*Client Action:* Mobile and Web interceptors catch `401` to clear session tokens and redirect to Sign In.

#### `403 Organization Required` (`ORGANIZATION_REQUIRED`)
Triggered when an authenticated user attempts to access organization-scoped resources without an active organization context (`organization_id` is null or missing).

```json
{
  "success": false,
  "error": {
    "code": "ORGANIZATION_REQUIRED",
    "status_code": 403,
    "message": "string"
  }
}
```
*Client Action:* **Must NOT trigger auto-logout.** Interceptor navigates user to Organization Creation or Organization Picker.

#### `403 Forbidden` (`FORBIDDEN`)
Triggered when an authenticated user belongs to an organization but lacks the necessary RBAC permissions for the action.

```json
{
  "success": false,
  "error": {
    "code": "FORBIDDEN",
    "status_code": 403,
    "message": "string"
  }
}
```

#### `404 Resource Not Found` (`RESOURCE_NOT_FOUND`)
Triggered when querying an entity (by UUID or human-readable key) that does not exist or has been soft-deleted.

```json
{
  "success": false,
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "status_code": 404,
    "message": "string"
  }
}
```

#### `409 Conflict` (`CONFLICT`)
Triggered when a unique index or state conflict occurs (e.g., duplicate project key, email collision, or an already active sprint in the same project).

```json
{
  "success": false,
  "error": {
    "code": "CONFLICT",
    "status_code": 409,
    "message": "string"
  }
}
```

#### `500 Internal Server Error` (`INTERNAL_SERVER_ERROR`)
Triggered when an unhandled server error occurs. Error stack trace is logged on the backend; a clean error contract is returned to the client.

```json
{
  "success": false,
  "error": {
    "code": "INTERNAL_SERVER_ERROR",
    "status_code": 500,
    "message": "string"
  }
}
```