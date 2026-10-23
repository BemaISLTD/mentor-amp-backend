# MentorAmp Phase 0 — Architecture Decision Log

**Version:** 0.1  
**Owner:** Samuel Ayinla  
**Status:** Working draft

## Decision states

- **Mandated:** required directly by the authoritative Launch 1.0 sources.
- **Proposed:** recommended technical interpretation awaiting team review.
- **Open:** a choice or owner input is still required.
- **Accepted:** reviewed and formally approved.
- **Superseded:** replaced by a later recorded decision.

## Decisions

| ID | State | Decision | Reason | Consequence/evidence | Reviewers required |
|---|---|---|---|---|---|
| ADR-001 | Mandated | Use the seven-layer architecture defined in the authoritative specification. | It separates UI, services, orchestration, model definition, execution selection, calculation, and persistence/trace. | `target-architecture.md` | Samuel, Noah, Sam, Actuarial, QA |
| ADR-002 | Mandated | UI never owns actuarial source-of-truth logic. | Calculations must be independently executable, testable, traceable, and reusable. | Boundary tests and frontend assessment | Samuel, Sam, QA |
| ADR-003 | Mandated | Formulas use semantic variables and remain independent of physical storage and execution technology. | The same formula must work with controlled sources and CPU/PySpark/GPU. | Formula/resolver contracts and import-boundary review | Samuel, Noah, Actuarial, QA |
| ADR-004 | Mandated | CPU is the correctness/reference execution path. | Transparent deterministic execution is needed before scale optimization. | CPU golden/regression evidence | Samuel, Actuarial, QA |
| ADR-005 | Mandated | PySpark and GPU are adapters to the common calculation definition, not alternate actuarial models. | Separate logic would invalidate parity evidence and multiply model risk. | Adapter conformance and parity tests | Samuel, QA |
| ADR-006 | Mandated | Dagster coordinates operational workflows but contains no actuarial formulas. | Orchestration and actuarial model ownership must remain separate. | Dagster boundary review and workflow evidence | Samuel, Noah, QA |
| ADR-007 | Mandated | Trace is emitted during execution. | Reconstructing trace later can omit actual inputs, overrides, and intermediates. | Trace contract and reference-run trace | Samuel, Noah, QA |
| ADR-008 | Mandated | Every completed run has an immutable reproducible manifest. | Results must identify all material data, model, code, environment, and backend versions. | Manifest schema and reproduction test | Samuel, Noah, QA |
| ADR-009 | Mandated | PostgreSQL stores controlled metadata; large datasets may use versioned columnar storage referenced by URI/fingerprint. | Large policy/scenario/cash-flow/trace data may not fit an all-relational design efficiently. | Storage strategy and recovery test | Samuel, Noah, QA |
| ADR-010 | Mandated | Product configurations reuse shared modules and one projection engine. | Product-specific engines would duplicate behavior and prevent consistent execution/trace. | Product modularity review | Samuel, Actuarial |
| ADR-011 | Mandated | Methodology-specific valuation remains explicit and distinct from product projection. | VM-21, LDTI, DAC, MRB, and ASC 815 may share cash flows but have different calculation/aggregation rules. | Methodology contracts and separate tests | Samuel, Actuarial, QA |
| ADR-012 | Proposed | Use technology-neutral domain ports between core/application services and infrastructure adapters. | This enforces the required dependency direction and improves testability. | `layer-ownership.md`; interface tests | Samuel, Noah |
| ADR-013 | Proposed | Treat approved versions as immutable; revisions create new draft versions. | Mutating approved content would make prior runs irreproducible. | Lifecycle contract and DB constraints | Samuel, Noah, Actuarial, QA |
| ADR-014 | Proposed | Represent large Run Outputs, Cash Flows, and Trace as versioned datasets, while keeping identity/status/index metadata in PostgreSQL. | Preserves scale and traceability without one relational row for every large output. | Storage ADR benchmark/recovery evidence | Samuel, Noah, QA |
| ADR-015 | Proposed | Use Derived Dataset as the only controlled output-as-input boundary. | Later runs must reference immutable, fingerprinted prior results rather than mutable queries. | Derived-dataset contract and lineage test | Samuel, Noah, QA |

## Open decisions

| ID | Decision needed | Options/considerations | Required owner(s) | Blocking |
|---|---|---|---|---|
| OPEN-ARCH-001 | Repository topology and shared-contract ownership. | Target document shows frontend/backend/engine/database/samples/tests/docs; current system uses separate frontend/backend repos. Options include monorepo, split repos with shared schema package, or current split with explicit ownership. | Samuel, Noah, Sam, Sponsor | CI, CODEOWNERS, imports, handoff |
| OPEN-ARCH-002 | Formula representation and controlled Python plug-in boundary. | Safe expression/DSL, AST-based representation, or controlled callable registry; requires validation, versioning, dependency metadata, tests, and sandbox/security analysis. | Samuel, Actuarial, QA | Formula Studio and Formula Registry contracts |
| OPEN-ARCH-003 | Synchronous vs asynchronous API/database strategy. | Current backend is synchronous SQLAlchemy; assess workload, orchestration, cancellation, streaming/status, and operational simplicity. | Samuel, Noah | API/service and persistence design |
| OPEN-ARCH-004 | PostgreSQL versus columnar storage thresholds. | Decide by volume, query pattern, trace level, retention, recovery, and local-first constraints. | Samuel, Noah, QA | Database schema and result storage |
| OPEN-ARCH-005 | Stable identifier and tenancy model. | UUID/ULID/business keys; optional company/tenant now versus extension point. | Samuel, Noah, Sponsor | Domain schema/API |
| OPEN-ARCH-006 | Lifecycle and status vocabularies. | Separate model approval lifecycle, import approval, run operational status, reconciliation status, and report approval. | Samuel, Noah, Sam, Actuarial, QA | Schemas, APIs, UI states |
| OPEN-ARCH-007 | Authentication/authorization implementation. | Local-first identity, shared deployment identity provider, role model, audit requirements. | Samuel, Noah, Sponsor | Security/API/approval workflows |
| OPEN-ARCH-008 | Exact GPU workload and supported runtime. | Select only after CPU profiling; Numba/CuPy or another approved implementation; hardware and fallback required. | Samuel, QA, Actuarial | GPU adapter/benchmark |
| OPEN-ARCH-009 | Trace levels and retention. | Balance explainability with data volume; define mandatory minimum and configurable detail. | Samuel, Noah, QA, Actuarial | Trace storage/UI/security |
| OPEN-ARCH-010 | Tolerance-profile and expected-result domain design. | Absolute/relative tolerances, units, applicability, approvals, versions, exception rules. | Actuarial, QA, Samuel | Reconciliation and acceptance |

## Review history

| Version | Date | Change | Review status |
|---|---|---|---|
| 0.1 | 2026-09-22 | Initial mandated, proposed, and open architecture decisions. | Pending team review. |
