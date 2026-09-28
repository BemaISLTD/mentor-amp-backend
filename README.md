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

This will automatically:
1. Start the PostgreSQL database on port `5432`.
2. Run all required Alembic database migrations.
3. Start the FastAPI application on port `8001`.
4. Mount the local `/backend` folder so changes to the code will hot-reload automatically.

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

## Running Tests
To run unit and integration tests inside the Docker container:
```bash
docker-compose exec -T api pytest
```
