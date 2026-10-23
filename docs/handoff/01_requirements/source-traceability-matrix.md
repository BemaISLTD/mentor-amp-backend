# MentorAmp Launch Release 1.0 — Source Traceability Matrix

**Version:** 0.1  
**Prepared:** 2026-09-22  
**Owner:** Samuel Ayinla — Technical Lead / Core Backend Architecture  
**Companion:** `master-requirement-tracker.md`

## Purpose

This matrix proves that every numbered section and table in the four authoritative source documents has a target location in the master requirement tracker. It also records source authority, overlaps, additions, conflicts, historical statements, and decisions still requiring approval.

## Source authority

| Priority | Code | Document | Treatment |
|---:|---|---|---|
| 1 | CAS | Consolidated Architecture and Delivery Specification | Primary Launch Release 1.0 technical and scope authority. |
| 2 | MP | Greenfield E2E Weekly SDLC Master Plan | Execution companion that assigns target weeks, evidence, gates, risks, and SDLC controls. |
| 3 | W0R | Week 0 Greenfield Project Initialization and Readiness Plan | Week 0 readiness, contract, handoff, and exit-gate detail. |
| 4 | ITD | Week 0 Independent Team Task Document | Individual ownership, collaboration, five-day execution, and evidence responsibilities. |

Where sources overlap, CAS defines launch scope; MP defines implementation sequence; W0R defines Week 0 readiness detail; ITD defines individual ownership. No lower-priority source removes a CAS requirement.

## Primary 19-page source coverage

The MP contains the authoritative page-by-page reconciliation for the 19-page CAS. The following mapping preserves that page coverage and connects each page to stable requirement IDs.

| CAS page | Source content and tables covered | Tracker IDs | Coverage status |
|---:|---|---|---|
| 1 | Document purpose, status, audience, launch decision, prepared date, and software-delivery/non-certification disclaimer. | LR1-GOV-001, LR1-GOV-013 | Mapped |
| 2 | Contents covering scope, architecture, platform, products, frameworks, data, execution, security, UX, testing, ownership, schedule, acceptance, expansion, confirmations, and appendices. | All LR1 categories | Mapped through sections below |
| 3 | Consolidated launch decision; controlled breadth; executable/tested/traceable verticals; modeling core, products, frameworks, execution, auditability, and deployment scope table. | LR1-GOV-003–005; LR1-CORE-*; LR1-PROD-*; LR1-FRM-*; LR1-EXEC-*; LR1-SEC-* | Mapped |
| 4 | Historical current-implementation assessment table and evidence-based schedule reset. | LR1-GOV-002, LR1-GOV-012, LR1-HND-001–008 | Mapped; historical status requires fresh assessment |
| 5 | Meaning of implemented, controlled breadth, and launch data/IP principles. | LR1-GOV-003–007 | Mapped |
| 6 | Seven-layer target architecture table and separation rules. | LR1-ARCH-001–015 | Mapped |
| 7 | Core modeling platform table: registries, Formula Studio, resolver, graph, projection, valuation, trace, reconciliation, controlled expressions/Python plug-ins. | LR1-CORE-001–030 | Mapped |
| 8 | Product-scope table and product modularity: SPIA, MYGA, FIA, RILA, IDI, VA, PRT, life readiness, shared modules. | LR1-PROD-001–009; LR1-ARCH-012 | Mapped |
| 9 | Framework table, framework separation, rollforward and attribution. | LR1-FRM-001–008; LR1-ARCH-013 | Mapped |
| 10 | Cash-flow/data dimensions table and ingestion requirements. | LR1-DATA-001–010 | Mapped |
| 11 | Dagster, PySpark, GPU, CPU evidence table and stable execution-interface rule. | LR1-EXEC-001–008; LR1-ARCH-011 | Mapped |
| 12 | Deployment model, hosted-service limits, security controls. | LR1-DATA-011–012; LR1-SEC-001–008; LR1-OPS-001–002 | Mapped |
| 13 | Seven application areas and their required functions. | LR1-APP-001–010; LR1-API-001–006 | Mapped |
| 14 | Test/governance control table, mandatory run manifest, and responsibility foundations. | LR1-TST-001–012; LR1-CORE-029–030; LR1-GOV-011 | Mapped |
| 15 | Team ownership/parallel-work rule and delivery-plan table. | LR1-GOV-011–012; LR1-SDLC-011–012 | Mapped |
| 16 | Scale/application/hardening workstreams and planning-range table. | LR1-EXEC-*; LR1-APP-*; LR1-OPS-*; LR1-GOV-012 | Mapped |
| 17 | Thirteen launch acceptance criteria and additive post-launch expansion. | LR1-GATE-001–013; LR1-GOV-010 | Mapped |
| 18 | Seven actuarial-confirmation decisions table. | LR1-ACT-001–007 | Mapped |
| 19 | Repository/database handoff checklist and prior-roadmap consolidation table. | LR1-HND-001–008; LR1-GOV-001–002 | Mapped |

## CAS section and table traceability

| CAS section | Content/tables accounted for | Tracker target | Notes |
|---|---|---|---|
| Cover and document-control table | Purpose, launch decision, users, date, draft status, disclaimer. | LR1-GOV-001, LR1-GOV-013 | Formal technical/scope approval remains required despite user-provided authoritative status. |
| Contents | All 17 sections and Appendices A/B. | Entire tracker | Navigation only; no standalone implementation requirement omitted. |
| §1 Executive decision and scope change | Consolidated scope, vertical implementation, controlled breadth; launch-scope-at-a-glance table. | LR1-GOV-003–005; CORE/PROD/FRM/EXEC/SEC categories | Earlier Phase 1/2/3 order is superseded. |
| §2 Current implementation assessment | Desktop/API/DB/engine/frontend/tests table and schedule implication. | LR1-GOV-002, LR1-GOV-012, LR1-HND-* | Historical June package assessment; current repos need new classification. |
| §3.1 Meaning of implemented | Five acceptance properties. | LR1-GOV-003, LR1-GATE-013 | Applied to every capability. |
| §3.2 Controlled breadth | One reference configuration per listed product/framework; additive expansion. | LR1-GOV-004–005 | — |
| §3.3 Launch data principles | Authorized data, IP restrictions, controlled storage. | LR1-GOV-006–007; LR1-SEC-* | — |
| §4 Target architecture | Seven-layer responsibility table and technology-independence rule. | LR1-ARCH-001–015 | — |
| §5 Core modeling platform | Nine-component capability table and controlled formula representation. | LR1-CORE-001–030 | — |
| §6 Product scope | Eight-row product/block minimum-capability table. | LR1-PROD-001–008 | — |
| §6.1 Product modularity | Shared modules and no separate engines. | LR1-PROD-009; LR1-ARCH-012 | — |
| §7 Frameworks | Seven-framework vertical-implementation table. | LR1-FRM-001–007 | Accounting conclusions still require appropriate review. |
| §7.1 Framework separation | Explicit basis/methodology separation. | LR1-ARCH-013 | — |
| §7.2 Rollforward and attribution | Required configurable change components and reconciliation. | LR1-FRM-008 | — |
| §8 Cash-flow/data model | Six dimension-group table. | LR1-DATA-001–006 | — |
| §8.1 Data ingestion | Formats, validation, mappings, fingerprints, rejects, logs. | LR1-DATA-007–010 | — |
| §9 Orchestration/distributed/acceleration | Four-technology evidence table and shared-interface rule. | LR1-EXEC-001–008; LR1-ARCH-011 | — |
| §10.1 Deployment model | Electron, FastAPI, PostgreSQL, Parquet, local Dagster. | LR1-SEC-001; LR1-DATA-011; LR1-OPS-001–002 | — |
| §10.2 Hosted services | Coolify/Vercel/Neon development-use statement. | LR1-SEC-002 | Actual configuration remains unverified. |
| §10.3 Minimum controls | Roles, encryption, secrets, least privilege, audit, retention, data restrictions. | LR1-SEC-003–008 | — |
| §11 Application/UX | Seven-screen launch-function table. | LR1-APP-001–007; LR1-API-* | — |
| §12 Testing/reconciliation/governance | Eight-control table. | LR1-TST-001–012 | — |
| §12.1 Run manifest | Complete mandatory field statement. | LR1-CORE-029–030 | — |
| §13 Team responsibilities | Five-owner responsibility table and parallel-work constraint. | LR1-GOV-011; LR1-SDLC-011 | — |
| §14 Delivery plan | Week 0–10 workstream/outcome table. | Target-week values across all tracker rows | — |
| §14.1 Planning ranges | Four starting-condition duration table and schedule warning. | LR1-GOV-012 | Estimate must be reset after assessment. |
| §15 Acceptance criteria | Thirteen numbered launch gates. | LR1-GATE-001–013 | — |
| §16 Post-launch expansion | Additive product, enterprise, ALM, AI, and optimization scope. | LR1-GOV-010 | Requires change control if moved into launch. |
| §17 Actuarial confirmations | Seven-decision table. | LR1-ACT-001–007 | All currently blocked pending owner input. |
| Appendix A Handoff | Eight-item repository/database handoff checklist. | LR1-HND-001–008 | — |
| Appendix B Consolidation | Prior Phase 1/2/3 treatment table and source list. | LR1-GOV-001–002 | Source files named here are references, not current launch authority. |

## MP section and table traceability

| MP section | Content/tables accounted for | Tracker target | Notes |
|---|---|---|---|
| Preamble | Greenfield principle, execution-companion relationship, team context. | LR1-GOV-001–003, LR1-GOV-011 | — |
| §1 Document control/source authority | Purpose, sources, additions, status, completion standard table. | LR1-GOV-001, LR1-GOV-003, LR1-GOV-012–013 | — |
| §1.1 Changes from previous plan | Greenfield sequencing, weekly evidence/gates, hardening, MYGA, frameworks, scale/application timing. | LR1-GOV-002, LR1-GOV-012; tracker target weeks | — |
| §2 Definition of Done | Executable/persisted/traceable/tested/documented criteria. | LR1-GOV-003, LR1-GATE-013 | — |
| §2.1 Controlled breadth | One reference configuration/additive expansion. | LR1-GOV-004–005 | — |
| §2.2 Data/IP | Authorized data and storage restrictions. | LR1-GOV-006–007; LR1-SEC-* | — |
| §3 Target scope | Eight-area scope table. | CORE, PROD, FRM, EXEC, DATA, APP, SEC, TST categories | — |
| §3.1 Products/reference capability | Eight-product outcome table. | LR1-PROD-001–009 | — |
| §4 Architecture contracts | Seven-layer must-own/must-not-own table and execution rule. | LR1-ARCH-001–015 | — |
| §5.1 Formula Registry | Metadata, representation, applicability, lifecycle, tests. | LR1-CORE-001–003 | — |
| §5.2 Formula Studio | Complete workflow checklist. | LR1-CORE-004–007 | — |
| §5.3 Variable Registry/Resolver | Metadata, context, sources, validation/trace. | LR1-CORE-008–012 | — |
| §5.4 Dependency Engine | DAG/order/errors/paths/impact. | LR1-CORE-013–015 | — |
| §5.5 Projection Engine | Load, loops, execution, state, outputs, status, aggregates, trace, determinism. | LR1-CORE-016–020 | — |
| §5.6 Valuation Engine | Cash flows, discounting, aggregation, risks, reserves, accounting, rollforwards. | LR1-CORE-021–022 | — |
| §5.7 Trace Engine | Required trace fields and API/UI. | LR1-CORE-023–024 | — |
| §5.8 Reconciliation Engine | Expected/actual, parity, periods, versions, totals, tolerances. | LR1-CORE-025–027 | — |
| §6 Shared Product Modules | Full shared-module list and no product engines. | LR1-PROD-009; LR1-ARCH-012 | — |
| §7 Framework work packages | Seven-framework table. | LR1-FRM-001–007 | — |
| §7.1 Framework separation | Distinct methodology rule. | LR1-ARCH-013 | — |
| §7.2 Rollforward/attribution | Ten component requirements and tolerance reconciliation. | LR1-FRM-008 | — |
| §8 Cash-flow/data model | Six-dimension table. | LR1-DATA-001–006 | — |
| §8.1 Ingestion | Formats, validation, mappings, logs, exports. | LR1-DATA-007–010; LR1-API-003 | — |
| §9 Execution technology | CPU/Dagster/PySpark/GPU implementation/evidence table and GPU gate. | LR1-EXEC-001–008 | — |
| §10 Storage/deployment/security | Local model, hosted limits, auth, encryption, secrets, audit, retention. | LR1-DATA-011; LR1-SEC-001–008; LR1-OPS-* | — |
| §11 Application/UX | Seven-screen functionality table. | LR1-APP-001–007 | — |
| §12 Testing/governance | Eight-control table. | LR1-TST-001–012 | — |
| §12.1 Run manifest | Mandatory field list. | LR1-CORE-029–030 | — |
| §13 Responsibilities/parallel work | Five-owner table and dependency constraint. | LR1-GOV-011; LR1-SDLC-011 | — |
| §14 Weekly plan overview | Week 0–10 objective/outcome table. | Target-week field throughout tracker | — |
| §14.0 Week 0 | Repository, tracker, architecture, environment, handoff, actuarial/QA setup, exit gate. | GOV, ARCH, SDLC, HND, ACT, TST requirements | Current phase. |
| §14.1 Week 1 | Contracts, canonical data, schema/APIs, shell/UI contracts, golden foundations, exit gate. | ARCH, CORE, DATA, API, APP, TST | — |
| §14.2 Week 2 | Registries, resolver, metadata, Formula Studio, unit/golden foundations, exit gate. | LR1-CORE-001–015; API-004; TST-002–003 | — |
| §14.3 Week 3 | Dependency/projection/trace/reconciliation, SPIA/MYGA, real UI run, exit gate. | CORE-013–030; PROD-001–002; API-005–006; APP-004–007 | — |
| §14.4 Week 4 | FIA/RILA modules/configuration/block/golden evidence, exit gate. | PROD-003–004, PROD-009; TST-003–004 | — |
| §14.5 Week 5 | Frameworks, rollforward, derived datasets, Dagster, UI/reporting, exit gate. | FRM-001–008; CORE-028; EXEC-002 | — |
| §14.6 Week 6 | Planner, PySpark, GPU, fallback/parity/benchmark, core app integration, exit gate. | EXEC-003–008; APP-002/004–007; API-003/005–006 | — |
| §14.7 Week 7 | IDI/VA/PRT, life readiness, reporting, audit/version coverage, exit gate. | PROD-005–008; FRM-007; APP-005 | — |
| §14.8 Week 8 | Seven-screen integration, packaging, migration/recovery, security, E2E tests, exit gate. | APP-001–010; SEC-*; OPS-*; TST-006/010 | — |
| §14.9 Week 9 | Hardening, regression, performance, recovery, security, clean install, runbooks, exit gate. | GOV-012; OPS-*; TST-007–011 | — |
| §14.10 Week 10 | Final 13-gate acceptance, handoff, release tag/package, sign-offs. | LR1-GATE-001–013; LR1-HND-*; SDLC-010 | — |
| §15 Existing comparison | Seven-class decision table and verification checklist. | LR1-GOV-002; assessment deliverables | Classification happens after target baseline. |
| §16 Page traceability | Nineteen-page table. | Primary page matrix above | Fully transcribed into this matrix. |
| §17 Final acceptance | Thirteen criteria and release gate. | LR1-GATE-001–013 | — |
| §18 Actuarial gate | Seven-decision table. | LR1-ACT-001–007 | — |
| §19 Planning ranges | Four-condition table and schedule rule. | LR1-GOV-012 | — |
| §20 Scope boundary | Explicit additive expansion list/change-control rule. | LR1-GOV-010 | — |
| §21 SDLC controls | Milestones, issues, links, branches, CI, tags, labels, integration, evidence. | LR1-SDLC-001–012 | — |
| §21.1 Cadence | Seven-cadence table. | LR1-SDLC-011–012 | — |
| §22 Architecture protection | Twelve technical-lead rules. | LR1-ARCH-002–014; LR1-EXEC-001/008 | — |
| §23 Risk register | Ten-risk trigger/mitigation table. | Initial blockers in tracker; future `11_project-management/risk-register.md` | No risk omitted. |
| §24 Weekly reporting | Ten-field report table. | LR1-SDLC-012 | — |
| §24.1 Requirement tracker | Nine-field example table. | This tracker and LR1-GOV-009 | Added category, notes, and change-control fields. |
| §25 Sign-off | Fourteen-item final team checklist. | LR1-GATE-*; HND-*; SDLC-010 | — |
| §25.1 Handoff | Eight-item handoff checklist. | LR1-HND-001–008 | — |
| §26 Final system principle | Coherent end-to-end system/common execution definition. | LR1-GATE-002, LR1-ARCH-011, LR1-GATE-008–010 | — |
| §26.1 Use instructions | Weekly issue creation, evidence, demos, gates, classification order. | LR1-SDLC-003–012; LR1-GOV-002–003 | — |
| §26.2 Source reconciliation | Authority, moved-in capabilities, workstream timing, schedule caveat. | LR1-GOV-001, LR1-GOV-012; all target weeks | — |

## W0R section and table traceability

| W0R section | Content/tables accounted for | Tracker target | Notes |
|---|---|---|---|
| Preamble and §1 | Greenfield rule and Week 0 objective. | LR1-GOV-002; Week 0 tracker work | — |
| §2 Principles | Target-before-current, classifications, tracking, actuarial dependencies, UI/engine/formula/CPU/IP/blocker rules. | LR1-GOV-*; LR1-ARCH-*; LR1-EXEC-001/008 | — |
| §3 Sources/baseline | Six-source treatment table and required 19-page coverage. | LR1-GOV-001; this matrix | — |
| §4 Master tracker | Eleven-field requirement table. | LR1-GOV-009; master tracker | All required fields represented. |
| §5 Architecture | Seven-layer table and logical flow. | LR1-ARCH-001–015 | — |
| §6 Domain objects | Full object list and documentation fields. | LR1-ARCH-015 | Catalogue deliverable pending. |
| §7 Database/persistence | Initial-table list and required DB decisions. | LR1-DATA-011–012; HND-003 | — |
| §8 GitHub/repository | Target folder structure and control list. | LR1-SDLC-001–010 | Current split-repo structure needs ADR. |
| §9 Environment/reproducibility | Backend/frontend/engine/future execution/reproducibility requirements. | LR1-OPS-001–002; EXEC-*; TST-010 | — |
| §10 API/services | Eight-area API table and schema/version/auth rules. | LR1-API-001–006 | — |
| §11.1 Formula Registry | Metadata list. | LR1-CORE-001–003 | — |
| §11.2 Formula Studio | Ten-capability list. | LR1-CORE-004–007 | — |
| §11.3 Variable Registry | Complete variable metadata list. | LR1-CORE-008–009 | — |
| §11.4 Resolver | Semantic lookup, nine sources, validation/error/trace. | LR1-CORE-010–012 | — |
| §11.5 Dependency Engine | DAG/order/error/path/impact list. | LR1-CORE-013–015 | — |
| §12 Projection/Valuation/Trace/Reconciliation | Canonical run sequence and four contract lists. | LR1-CORE-016–027 | — |
| §13 Data/cash-flow model | Six-dimension example table and format/validation requirements. | LR1-DATA-001–010 | — |
| §14 Product/framework readiness | Product/framework/shared-module lists. | LR1-PROD-*; LR1-FRM-* | — |
| §15 Execution baseline | Four-path table and common-definition rule. | LR1-EXEC-001–008 | — |
| §16 Frontend/UX | Seven-screen Week 0 definition table and UI-state mapping. | LR1-APP-001–009 | — |
| §17 Testing strategy | Nine-level table. | LR1-TST-001–012 | — |
| §17.1 Run manifest | Nineteen mandatory field groups. | LR1-CORE-029–030 | — |
| §18 Product-owner inputs | Eight-reference input table. | LR1-PROD-*; LR1-FRM-*; LR1-TST-003–004 | Blocked pending material. |
| §18.1 Seven confirmations | Seven-decision list. | LR1-ACT-001–007 | — |
| §19 QA setup | Eleven QA governance items. | LR1-TST-001, LR1-TST-008–012 | — |
| §20 Existing assessment | Six-classification table and inventory list. | LR1-GOV-002; assessment matrix | — |
| §21 Security/data/IP | Eleven security/readiness items. | LR1-GOV-006–007; LR1-SEC-003–008 | — |
| §22 Responsibilities | Five-role table and parallel-work rule. | LR1-GOV-011 | — |
| §23 Week 0 sequence | Five-day focus/output table. | TODO five-day plan; target W0 rows | — |
| §24 Complete deliverables | Project, architecture, DB, backend, frontend, actuarial, QA, assessment, security checklists. | All tracker categories and handoff folders | — |
| §25 Exit gate | Seventeen Week 0 exit requirements and readiness callout. | Week 0 gate audit; GOV/ARCH/CORE/SDLC/ACT/TST/HND requirements | — |
| §26 Handoff package | Eleven-folder handoff list. | Evidence-path columns throughout tracker | — |
| §27 Sign-off | Six-role sign-off table. | Final readiness/sign-off record | Pending stakeholders. |
| §28 Use instructions | Milestone/issues/IDs/evidence/standup/classification/gate steps. | LR1-SDLC-003–012; LR1-GOV-002/009 | — |

## ITD section and table traceability

| ITD section | Content/tables accounted for | Tracker target | Notes |
|---|---|---|---|
| Preamble and §1 | Greenfield/existing-system rule and Week 0 purpose. | LR1-GOV-002 | — |
| §2 Success definition | Fifteen Week 0 success outcomes. | Week 0 gate audit and all tracker categories | — |
| §3 Team assignment | Five-role primary/secondary responsibility table. | LR1-GOV-011 | — |
| §4 Universal rule | Eight-step verification table. | Tracker operating rule; assessment package | — |
| §5 Classification | Six-classification rule table. | LR1-GOV-002; assessment matrix | — |
| §6 Samuel tasks | Requirements, architecture, domain, contracts, execution, reviews, assessment, SDLC, backlog/readiness. | GOV, ARCH, CORE, EXEC, SDLC, HND/GATE requirements | Owner assignment preserved. |
| §7 Noah tasks | Environment, DB, APIs, services, integration, persistence, assessment, gaps. | DATA, API, SEC, OPS, APP, TST, HND requirements | Owner assignment preserved. |
| §8 Sam tasks | React/Electron, seven screens, states, component/API map, UI assessment. | LR1-APP-001–010; SDLC/TST support | Owner assignment preserved. |
| §9 Product-owner tasks | Eight product/framework input rows. | PROD, FRM, TST-003–004 | — |
| §9.1 Actuarial confirmations | Seven-decision list. | LR1-ACT-001–007 | — |
| §10 QA tasks | Eighteen evidence/test/governance tasks. | LR1-TST-001–012; GATE-* | — |
| §11 Shared Samuel/Noah | Ten architecture/schema/API/contract/assessment/gap tasks. | ARCH, CORE, DATA, API, GOV-002, SDLC-007 | Collaboration requirement. |
| §12 Shared developers | Thirteen common rules. | GOV, ARCH, EXEC, SDLC, SEC requirements | — |
| §13 Five-day plan | Day/focus/role/output table. | TODO five-day plan; Week 0 target | — |
| §14 SDLC minimum | Sixteen repository/process/evidence controls. | LR1-SDLC-001–012; CORE-029 | — |
| §15 Required evidence | Thirteen-area evidence table. | Evidence paths throughout tracker | — |
| §16 Exit checklist | Seventeen numbered readiness criteria. | Week 0 exit audit | — |
| §17 Week 0→1 handoff | Six-classification action table and issue-field rule. | Assessment classification; LR1-SDLC-007–008 | — |
| §18 Sign-off | Six-role status/evidence/signature table. | Readiness sign-off record | — |
| §19 Important rules | Correctness, CPU, common logic, independence, IP, blockers, evidence, scope. | GOV, ARCH, EXEC, SEC requirements | — |

## Cross-source additions and reconciliation decisions

| Topic | Source relationship | Decision/status |
|---|---|---|
| Primary scope | CAS is primary; all other documents explicitly defer to it for launch scope. | Recorded in LR1-GOV-001. |
| Earlier Phase 1/2/3 plans | Useful architecture references; sequential delivery order is superseded. | Do not use them to defer Formula Studio, frameworks, Dagster, PySpark, or GPU beyond Launch 1.0. |
| Current implementation claims | CAS assessment is historical; current backend/frontend now exist but lack sufficient verification evidence. | Perform fresh classification; no automatic reuse credit. |
| Phase 0 vs Week 0 wording | Individual document uses Phase 0; authoritative sources use Week 0 for the same initialization/readiness gate. | Treat as the same current delivery stage unless sponsor directs otherwise. |
| Repository layout | W0R proposes `frontend/`, `backend/`, `engine/`, `database/`, `samples/`, `tests/`, `docs/`; current code is split across two repos with engine code under backend. | Requires architecture/repository-boundary ADR before finalizing SDLC controls. |
| Desktop target | CAS requires Electron + React; current frontend is a Vite/React web application. | Classify current frontend after build review; create Electron packaging/integration work if absent. |
| Hosted services | CAS mentions Coolify, Vercel, and Neon only as reported development/demo services. | Verify actual configuration before recording architecture or storing data. |
| Schedule | MP is a ten-week target; CAS provides 8–20+ week ranges based on verified baseline. | Evidence-based re-estimate follows classification and dependency review. |
| Product/framework correctness | Developers cannot invent expected values, formula intent, tolerances, or methodology. | ACT-001–007 and reference packages remain explicit blockers. |
| Accounting/regulatory meaning | Documents are software delivery specifications, not opinions or certifications. | Preserve disclaimer and obtain appropriate accounting/actuarial review. |

## Coverage audit

| Check | Result |
|---|---|
| Four authoritative documents extracted and read completely | Pass |
| Headers and footers included | Pass |
| All 68 source tables preserved in extraction | Pass |
| CAS pages 1–19 mapped | Pass |
| CAS sections and appendices mapped | Pass |
| MP sections 1–26.2 mapped | Pass |
| W0R sections 1–28 mapped | Pass |
| ITD sections 1–19 mapped | Pass |
| Thirteen launch acceptance criteria assigned stable IDs | Pass |
| Seven actuarial confirmations assigned stable IDs | Pass |
| Repository/database handoff items assigned stable IDs | Pass |
| Stakeholder review and approval complete | Pending |

## Review history

| Version | Date | Change | Review status |
|---|---|---|---|
| 0.1 | 2026-09-22 | Initial complete source-to-target crosswalk. | Pending Samuel, Noah, Sam Yung, Actuarial Product Owner, QA, and sponsor review. |
