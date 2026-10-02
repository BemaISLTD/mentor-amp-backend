# MentorAmp Backend

This repository contains the core Python/FastAPI backend and PostgreSQL database for the MentorAmp actuarial engine.

## Prerequisites
- **Docker** and **Docker Compose** installed.

## Environment Setup
1. Clone this repository.
2. Copy the `.env.example` file to `.env`:
   ```bash
   cp backend/.env.example backend/.env
   ```
   *(By default, this connects to the local dockerized Postgres database. No changes are required for local development.)*

## Running the Application

### Option A: With Docker (Recommended)
Spin up the entire stack (PostgreSQL Database + FastAPI Server) using Docker Compose:

```bash
docker-compose up --build -d
```

The Docker image includes the synthetic SPIA samples and embeds the Git commit.
Each submitted run package also records a digest of the actual backend code,
including bind-mounted local changes. Build from a Git checkout; run submission
is refused when neither a commit nor a code digest is available.

This will automatically:
1. Start the PostgreSQL database on port `5432`.
2. Run all required Alembic database migrations.
3. Start the FastAPI application on port `8001`.
4. Mount the local `/backend` folder so changes to the code will hot-reload automatically.

Before upgrading a database that has M1 run results or trace rows, export those
rows to artifact storage. The migration deliberately stops if either legacy
table contains data; it does not silently discard historical outputs. A fresh
database or a database already using Parquet artifacts upgrades normally.

### Option B: Without Docker (Local Virtual Environment)
If you prefer to run the API directly on your machine, you can use a virtual environment. You will still need a PostgreSQL database running locally (or via Docker just for the DB).

1. Ensure you have Python 3.11+ installed.
2. Navigate into the `backend` directory:
   ```bash
   cd backend
   ```
3. Create and activate a virtual environment:
   ```bash
   # Windows
   python -m venv venv
   venv\Scripts\activate
   
   # Mac/Linux
   python3 -m venv venv
   source venv/bin/activate
   ```
4. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
5. Apply database migrations:
   ```bash
   alembic upgrade head
   ```
6. Start the server with hot-reloading:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

### Accessing the API
- **Base API URL:** `http://localhost:8001` (Docker) or `http://localhost:8000` (Local)
- **Versioned API prefix:** `/v1` (for example, `GET /v1/projects/`)
- **Run API:** `/v1/run-sets` and `/v1/runs` use the consolidated M1 contract. Older backlog run routes are deprecated under `/v1/legacy-runs` and restricted to administrators.
- **Health check:** `GET /health` (intentionally unversioned for infrastructure probes)
- **Swagger Documentation:** `http://localhost:8001/docs` (Docker) or `http://localhost:8000/docs` (Local)

### Initial Administrator and Authentication

On a new database, create the first administrator once:

```bash
curl -X POST http://localhost:8001/v1/auth/bootstrap \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","full_name":"Admin User","password":"replace-with-a-secure-password"}'
```

After the first user exists, the bootstrap endpoint returns `409` and only an
administrator can create additional users through `POST /v1/users/`.

Obtain a bearer token using OAuth2 form fields (`username` is the email):

```bash
curl -X POST http://localhost:8001/v1/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin@example.com&password=replace-with-a-secure-password"
```

Set a unique `JWT_SECRET_KEY` of at least 32 bytes whenever `DEBUG=false`.

### Analytical Artifact Storage

Projection outputs and debug traces are written as compressed Parquet files.
PostgreSQL stores immutable run manifests and artifact metadata rather than
individual calculated values.

Local Docker development uses `/app/artifacts`, backed by the ignored
`backend/artifacts/` workspace directory. Configure it with:

```text
ARTIFACT_STORAGE_BACKEND=local
ARTIFACT_STORAGE_PATH=/app/artifacts
CODE_VERSION=<deployment revision>
```

### Projection Runs

Authenticated users with the `admin` or `actuary` role can use:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/v1/runs/` | Queue a projection run |
| `GET` | `/v1/runs/` | List and filter runs |
| `GET` | `/v1/runs/{run_id}` | Poll run status |
| `GET` | `/v1/runs/{run_id}/manifest` | Read the immutable input manifest |
| `GET` | `/v1/runs/{run_id}/summary` | Read result summary metrics |
| `GET` | `/v1/runs/{run_id}/results` | Read filtered, paginated outputs |
| `GET` | `/v1/runs/{run_id}/events` | Read filtered debug traces |
| `GET` | `/v1/runs/{run_id}/reports/reconciliation` | Compare against a baseline run |

The current local adapter executes queued runs in an in-process FastAPI
background task. A durable external worker queue is still required before
multi-instance production deployment.

Reconciliation requires two completed runs from the same project. Pass the
prior run as `baseline_run_id`; optional policy, scenario, variable, and month
filters are applied before calculating detail variances and variable bridges.

### Products and Assets

Authenticated `admin` and `actuary` users can manage:

- `/v1/products/` for project-scoped product definitions;
- `/v1/products/{product_id}/mappings` for effective-dated source mappings;
- `/v1/asset-positions/` for dated product asset positions.

Product codes are unique within a project. Deletes are soft deletes and product
deletion also retires its active mappings and asset positions.

### Dashboard Statistics

Authenticated `admin` and `actuary` users can request project-level statistics
from `GET /v1/dashboard/stats?project_id={project_id}`. The response summarizes
products, the latest asset-position snapshot, projection runs, and in-force
file volumes. Pass `as_of_date=YYYY-MM-DD` to select a historical asset
snapshot instead.

## Running Tests
To run unit and integration tests inside the Docker container:
```bash
docker-compose exec -T api pytest
```
