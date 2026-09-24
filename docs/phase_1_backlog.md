# Phase 1: Actionable Backlog

This document serves as the final deliverable for Phase 0 (Day 5). It compiles all the gaps discovered during our Verify -> Compare -> Classify process into an extensive, detailed, yet easy-to-understand backlog.

The goal of Phase 1 is to close the gap between the backend's current state and the Launch Release 1.0 requirements defined by the frontend team.

---

## 1. Environment & Testing

> [!TIP]
> **Context:** The environment is mostly healthy, but testing is completely absent.

*   **[BUILD] Implement the Pytest Suite**
    *   **Detail:** The `pytest` command currently returns `0 items collected`. We must build out a full test suite covering database connectivity, model validation, and API endpoint behavior.
*   **[IMPROVE] Database Seeding Scripts**
    *   **Detail:** There are currently no scripts to seed the database with mock users, products, or roles. We need to write a `seed.py` script so developers can instantly spin up a populated local environment.

---

## 2. API Contract & Security

> [!WARNING]
> **Context:** The API currently has zero security, no versioning, and several major missing domains.

*   **[BUILD] Global Authentication & Authorization (JWT)**
    *   **Detail:** Every endpoint is completely open to the public. Implement an OAuth2/JWT middleware and a `/auth/me` endpoint. Secure all routers so that only authenticated actuaries/admins can trigger runs or upload files.
*   **[IMPROVE] API Versioning & Error Handling**
    *   **Detail:** Move all endpoints under a `/v1/` prefix (e.g., `/v1/projects`). Implement a global Exception Handler so that when the backend throws an error, it returns a standardized JSON payload (e.g., `{"error": {"code": "NOT_FOUND", "message": "..."}}`) that the frontend can reliably parse.
*   **[BUILD] File Record Viewers**
    *   **Detail:** Any UI will require spreadsheet-style modal viewers to look at individual rows of data. We must build pagination and filtering to support this.

> [!NOTE]
> **Frontend Dependent API Refactors:** The following refactors were identified to match the existing frontend mock API. If the current frontend is scrapped, these namespaces (`/imports` and `/formulas`) can likely remain as they are, and the new UI can simply adapt to the backend's current structure.

*   **[REPLACE] Refactor `/imports` to `/files` Data Manager** (Conditional)
    *   **Detail:** The backend uses isolated routes (`/imports/inforce`). The old frontend expects a unified Data Manager API (`/files`). 
*   **[REFACTOR] Refactor `/formulas` to `/tables` Registry** (Conditional)
    *   **Detail:** The backend built a `/formulas` namespace, but the old frontend expects an interactive Actuarial Table Registry at `/tables`. 

---

## 3. Database Schema & Domain Modeling

> [!IMPORTANT]
> **Context:** The database schema is missing several critical domain areas required by the product specs.

*   **[BUILD] User & RBAC Schemas**
    *   **Detail:** Build the `users`, `roles`, and `permissions` tables required to support the new Authentication API.
*   **[BUILD] Products & Assets Schemas**
    *   **Detail:** Build the `products`, `asset_positions`, and `product_mappings` tables so the frontend can populate its Product Mix and Dashboard charts.
*   **[BUILD] Actuarial Workflow Schemas**
    *   **Detail:** Build the `rollforward_templates`, `rollforward_jobs`, and `rollforward_steps` tables to support automated month-end reporting workflows.
*   **[BUILD] Audit Logs**
    *   **Detail:** Create a system-wide `audit_logs` table to track who modified what (e.g., "User A updated Assumption B at 10:00 AM").

---

## 4. Actuarial Execution & Big Data (The "Run" Engine)

> [!CAUTION]
> **Context:** This is the most critical architectural risk. If left as-is, the engine will crash the PostgreSQL database at scale.

*   **[REPLACE] Offload `run_outputs` and `trace_logs` to Parquet/S3**
    *   **Detail:** Currently, the database tries to write cashflow outputs and debug traces as individual rows in PostgreSQL. At production scale, this will generate billions of rows and crash the database. We MUST remove these tables from PostgreSQL and re-architect the engine to write compressed Parquet files directly to S3 or a local data lake.
*   **[BUILD] Missing Execution APIs**
    *   **Detail:** The `runs.py`, `results.py`, and `trace.py` files exist but have 0 bytes of code. We must build the actual endpoints that trigger projection runs, check their status, and fetch the resulting cashflows.
*   **[BUILD] Run Manifests (Reproducibility)**
    *   **Detail:** When a run is executed, the `runs` table must lock in a "Manifest"—an immutable snapshot of the exact data file versions and assumption versions used. This guarantees that if an assumption is changed later, old runs remain perfectly reproducible.
*   **[BUILD] Reconciliation Service (Reserve Bridges)**
    *   **Detail:** The frontend requires a "Prior vs Current" variance report. We must build a reconciliation engine that can compare two Parquet output datasets and calculate the exact financial variance between them.
