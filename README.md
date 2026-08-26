# FastAPI JWT Auth

A reusable JWT authentication template for FastAPI applications. Clone it, configure your database, and run `docker compose up` — tables are created automatically and sample users are seeded.

## Features

- User login with email & password
- Access token & refresh token pair
- Token refresh endpoint
- Logout with refresh token revocation
- Role-based authorization (`admin`, `user`)
- Protected endpoints via Bearer token
- Multi-database support (PostgreSQL, MySQL, SQL Server)
- Password hashing with Argon2
- Structured logging with configurable log levels
- Try-except error handling on all endpoints and service functions
- Unit tests with pytest (27 tests)
- CI pipeline with GitHub Actions

## Tech Stack

- Python 3.12+
- FastAPI 0.141
- SQLAlchemy 2.0 + Alembic
- PostgreSQL (psycopg 3) / MySQL (mysql-connector-python) / SQL Server (pyodbc)
- PyJWT (HS256)
- pwdlib (Argon2)
- Docker & Docker Compose
- pytest + httpx (testing)
- GitHub Actions (CI)

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/your-org/fastapi-jwt-auth.git
cd fastapi-jwt-auth
```

### 2. Configure environment

Copy the example env file and update the values:

```bash
cp .env.example .env
```

Choose your database and set the `DATABASE_URL`:

**PostgreSQL:**
```env
DATABASE_URL=postgresql+psycopg://username:password@localhost:5432/application_db
```

**MySQL:**
```env
DATABASE_URL=mysql+mysqlconnector://username:password@localhost:3306/application_db
```

**SQL Server:**
```env
DATABASE_URL=mssql+pyodbc://username:password@localhost:1433/application_db?driver=ODBC+Driver+17+for+SQL+Server&TrustServerCertificate=yes
```

Common settings:
```env
JWT_SECRET_KEY=your-secret-key-here
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
```

### 3. Run with Docker

```bash
docker compose up --build
```

On first startup the entrypoint will:

1. Wait for the database to be ready
2. Run Alembic migrations (creates `users` and `refresh_tokens` tables)
3. Insert two sample users
4. Start the FastAPI server on port 8000

### 4. Open the API docs

```
http://localhost:8000/docs
```

## Database Support

Models use only database-agnostic types (`String`, `DateTime`, `Boolean`) so they work across all three databases without changes.

| Database | Driver | Connection Prefix | Notes |
|----------|--------|-------------------|-------|
| PostgreSQL | psycopg 3 | `postgresql+psycopg://` | Recommended for production |
| MySQL | mysql-connector-python | `mysql+mysqlconnector://` | — |
| SQL Server | pyodbc | `mssql+pyodbc://` | Requires ODBC Driver 17 on host |

> When running in Docker, the Dockerfile includes the SQL Server ODBC driver. For local development, install [ODBC Driver 17 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) on your machine.

## Sample Users (seeded automatically)

| Name       | Email                    | Password  | Role  |
|------------|--------------------------|-----------|-------|
| Test User  | testuser@example.com     | Test@123  | user  |
| Demo User  | demouser@example.com     | demo@123  | admin |

## API Endpoints

| Method | Endpoint              | Auth Required | Role        | Description                              |
|--------|-----------------------|---------------|-------------|------------------------------------------|
| POST   | `/auth/login`         | No            | —           | Login, returns access + refresh tokens   |
| POST   | `/auth/refresh`       | No            | —           | Exchange refresh token for new access token |
| POST   | `/auth/logout`        | No            | —           | Revoke a refresh token                   |
| GET    | `/auth/profile`       | Yes           | Any         | Get current user info                    |
| GET    | `/auth/admin/profile` | Yes           | admin       | Admin-only profile                       |
| GET    | `/auth/user/profile`  | Yes           | user        | User-only profile                        |
| GET    | `/health_check`       | No            | —           | Health check                             |

## Usage Example

### Login

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email_address":"testuser@example.com","password":"Test@123"}'
```

### Access a protected endpoint

```bash
curl http://localhost:8000/auth/profile \
  -H "Authorization: Bearer <access_token>"
```

### Refresh the access token

```bash
curl -X POST http://localhost:8000/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"<refresh_token>"}'
```

### Logout (revoke refresh token)

```bash
curl -X POST http://localhost:8000/auth/logout \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"<refresh_token>"}'
```

### Use role-based endpoints

```bash
# Admin only
curl http://localhost:8000/auth/admin/profile \
  -H "Authorization: Bearer <admin_access_token>"

# User only
curl http://localhost:8000/auth/user/profile \
  -H "Authorization: Bearer <user_access_token>"
```

## Project Structure

```
.
├── .github/workflows/
│   └── ci.yml                     # GitHub Actions CI pipeline
├── alembic/                       # Database migrations
│   ├── env.py
│   └── versions/
├── src/
│   ├── config.py                  # Env vars, logging setup
│   ├── database.py                # SQLAlchemy engine & session
│   ├── insert_user.py             # Seed script
│   ├── main.py                    # FastAPI app entry point
│   ├── jwt_auth/
│   │   ├── api.py                 # Auth route handlers
│   │   ├── models.py              # SQLAlchemy models (DB-agnostic)
│   │   └── schema.py              # Pydantic schemas
│   ├── services/jwt_auth/
│   │   ├── dependancy.py          # get_current_user, require_role
│   │   └── service.py             # Auth business logic
│   └── utils/
│       ├── jwt_handler.py         # JWT encode/decode
│       └── password_helper.py     # Argon2 hashing
├── tests/
│   ├── conftest.py                # Shared fixtures and mocks
│   ├── test_auth.py               # Endpoint tests (19 tests)
│   └── test_dependency.py         # Dependency tests (8 tests)
├── docker-compose.yaml
├── dockerfile
├── docker-entrypoint.sh
├── .env.example
├── pyproject.toml
└── README.md
```

## Testing

### Run tests locally

```bash
poetry install --with test
poetry run pytest -v
```

### Test coverage

| File | Tests | What it covers |
|------|-------|---------------|
| `test_auth.py` | 19 | Login, refresh, logout, profile, admin/profile, user/profile, health check |
| `test_dependency.py` | 8 | get_current_user, require_role (valid token, invalid token, role checks) |
| **Total** | **27** | All endpoints and auth dependencies |

Tests use mocked database sessions and service functions — no real database required.

## CI Pipeline

GitHub Actions runs tests automatically on every push and pull request to `develop`.

```yaml
# .github/workflows/ci.yml
- Installs Python 3.12 and Poetry
- Sets JWT_SECRET_KEY and DATABASE_URL env vars
- Runs all 27 tests with pytest
```

No manual setup required — just push the workflow file.

## Error Handling

All endpoints and service functions are wrapped in try-except blocks:

- **Expected errors** (401, 403) — re-raised as `HTTPException`
- **Unexpected errors** (DB failures, etc.) — logged with full traceback via `logger.exception()`, returns `500 Internal Server Error`

Example log output:

```
2026-08-19 10:00:00 | ERROR    | src.jwt_auth.api | Error during login for: test@example.com
Traceback (most recent call last):
  File "src/jwt_auth/api.py", line 78, in login
    ...
```

## Logging

Logs are written to stdout with the format:

```
2026-08-19 10:00:00 | INFO     | src.jwt_auth.api | User logged in: testuser@example.com
```

Set the `LOG_LEVEL` env var to control verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`).

## Development (without Docker)

```bash
pip install poetry
poetry install --with test
cp .env.example .env   # update DATABASE_URL to point to your local database
alembic upgrade head
python -m src.insert_user
uvicorn src.main:app --reload
```

## License

MIT
