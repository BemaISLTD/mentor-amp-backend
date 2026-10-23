# Frontend Source Review — Sam Yung's push `39913d6` vs the Consolidated Specification

**Date:** 2026-09-29 (evening)
**Reviewer:** AI assistant for Samuel Ayinla
**Frontend:** `BemaISLTD/mentor-amp` `main` at `39913d6` ("volt-1.0_push")
**Authority compared against:** `authoritative documents/extracted/MentorAmp_Launch_Release_1_Consolidated_Architecture_and_Delivery_Specification.full.md` (CAS)

## 1. What was read

- The CAS in full: all 17 sections plus Appendices A and B (458 lines).
- Every frontend source file: `src/App.tsx`, `src/main.tsx`, `src/types.ts`, `src/data/fixtures.ts`, `src/context/*` (3 files), `src/components/layout/*` (5 files), `src/components/ui/*` (9 files), and all 16 files in `src/pages/`.
- Not read line by line: `src/index.css` (82 KB of styling only).

This complements the earlier review of the *deployed site* (`2026-09-29-frontend-backend-integration-plan.md`, `2026-09-29-deployed-ui-control-inventory.md`). That review looked at what users see. This one looks at the *source code* behind it.

## 2. How the frontend is built (facts from the source)

| Fact | Evidence | Why it matters |
|---|---|---|
| No URLs or router. The screen is a React state variable (`activeScreen`), and sub-screens are local `useState` views. | `src/context/AppContext.tsx`, `src/App.tsx` `renderPage()` | A reload always returns to Dashboard. Nothing can be bookmarked or shared, and selected IDs (model, run) are lost. Integration needs real routes such as `/projects/:projectId/models/:modelId`. |
| No API layer, no login, no current user, no Project. | No `fetch`, `axios`, or env usage; `Topbar.tsx` hard-codes "Andrew Johnson · Actuary" and the context "MYGA · Model v3.2 · Q4-2024 GAAP Reserve — MYGA Base · Base scenario · 2026-12-31". | The backend already has `/v1/auth/token`, `/v1/auth/me`, and `/v1/projects`; the UI has nowhere to use them yet. |
| All data is hard-coded in each page file, not only in `fixtures.ts`. | e.g. `libraryModels` in `ModelsPage.tsx`, `inputRows` in `InputScreens.tsx`, `runSets` in `ExecutionScreens.tsx`. | Mock removal is page by page. There is no single mock service to swap out. |
| Every button either switches a local view or shows a toast via `notify(...)`. | All pages. | No action persists anything. |
| Dependencies are only `react`, `react-dom`, `lucide-react` (plus Vite, Tailwind, TypeScript, oxlint for development). | `package.json` | The README's claims of React Router, TanStack Table, React Hook Form + Zod, Recharts, Radix, and shadcn are **not true**. Charts are CSS shapes, not data-driven charts. |
| The Electron desktop shell and typed API client were removed. | Commit `85f1952` had `apps/desktop/electron/main.ts`, `preload.ts`, `electron-builder.json5`, `src/lib/api/client.ts`, `healthApi.ts`, `systemStatusApi.ts`. They were deleted in `b9f70a7` on 2026-09-19 ("Replace legacy app with MentorAmp frontend", authored from the Samuelbema account). | CAS §1, §10.1, and §15 require a local-first Electron application. The current app is web-only (Netlify). This needs a recorded decision. |
| Dead code. | `src/pages/AnalysisPages.tsx` and `src/pages/AuthoringPages.tsx` export 9 older screen versions (`RunsPage`, `ResultsPage`, `ReportsPage`, `InvestigatePage`, `MentorPage`, `InputsPage`, `ScenariosPage`, `FormulasPage`, `ProjectionsPage`) that nothing imports. `ComingSoonPage` is imported but unreachable, because every `ScreenName` has a case. | Confusing for anyone integrating. Delete, or mark as archived. |
| The build is not reproducible with npm. | `npm ci` fails: lockfile out of sync; `bun.lock` also present. | See BLK-015. |

## 3. Screen inventory (source)

| Sidebar item | File | Sub-views in source |
|---|---|---|
| Dashboard | `DashboardPage.tsx` | metrics, continue-investigation, recent runs, recent investigations, quick actions |
| Models | `ModelsPage.tsx` → `ModelWorkspaceScreens.tsx` → `CrossModelScreens.tsx` | library + detail panel → workspace (hierarchy), basis detail, published outputs, relationship map, inheritance wizard → output mapping, cross-model trace, run linkage |
| Inputs | `InputScreens.tsx` | library, mappings, liability inforce, asset inforce, assumption tables, factor tables, projection inputs, validation center |
| Scenarios | `ScenarioScreens.tsx` | library, detail, generator library, generator editor, generated batch, attach to Projection Set, compare |
| Formulas | `FormulaScreens.tsx` | formula groups, formula detail, inheritance map, override compare (restore/rebase/fork), new block/profile wizard |
| Projections | `ProjectionScreens.tsx` | Projection Set library, create/edit, validation |
| Runs | `ExecutionScreens.tsx` | Run Set library, create/edit, execution monitor (pause/resume/cancel, GPU/CPU), recovery/retry |
| Results | `ResultScreens.tsx` | explorer, run history, compare runs, driver/variance waterfall, CSV export (hard-coded) |
| Reports | `ReportScreens.tsx` | library, definition/preview, Run Set attachment, completed report + export |
| Investigate | `InvestigationScreens.tsx` | trace explorer (backward/forward, pins), saved investigations, scratchpad notebook, compare with another run |
| Mentor | `MentorScreens.tsx` | contextual chat (keyword-matched mock replies, labelled "mock AI conversation"), history |
| Admin / Settings | `SettingsPage.tsx` | 5 settings rows (toast only), hard-coded "All systems operational" |

## 4. CAS §11 seven application areas vs the source

| CAS area and required functions | Where it is in the UI | Coverage | Missing against CAS |
|---|---|---|---|
| **Workspace** — projects, blocks, products, valuation dates, model versions, users, recent runs | Dashboard + Models (+ Block/Profile/Basis inside a model) | Partial | **Projects**, users, and a project/workspace switcher are absent. Model "Versions (5)" and "Access (3)" tabs are labels only. |
| **Data manager** — import, map, validate, preview, compare, version, approve: inforce, assumptions, factors, scenarios, assets, actuals, **expected results** | Inputs | Partial | No **actuals** or **expected-results** import (Reconciliation needs them). No compare or approve. "Import input" / "Import new version" are toasts. |
| **Formula Studio** — search, **edit**, **test**, compare, document, visualize dependencies, version, **submit, approve, activate, roll back** | Formulas | Partial | No formula editor (expression is display-only); "12 tests passing" is text, not test execution; no submit/approve/activate/rollback lifecycle. Inheritance/override/fork is a UI concept that needs an agreed backend lifecycle. |
| **Run center** — product, basis, **methodology**, data versions, scenario set, output package, trace level, **execution backend**; submit and monitor | Projections (Projection Set) + Runs (Run Set) | Partial, well-structured | No methodology selection (only basis). Execution choices are GPU/CPU cards (the dead-code version also shows "Distributed"); no explicit PySpark option. Pause/resume/checkpoint are shown; CAS does not require them, so their cost needs a decision. |
| **Results explorer** — filter policy and aggregate results, compare runs, review **reserve components, attribution, rollforwards, disclosures**, export | Results | Partial | No policy-level drill-down; no reserve components, rollforward, or disclosure views; only one driver waterfall. Export is a hard-coded CSV. |
| **Trace and debug** — calculation path, formulas, input values, sources, **lookup keys, intermediate results, errors** | Investigate → Trace Explorer (+ cross-model trace) | Partial | Shows a 4-node path only; no lookup keys, intermediate values, or error view. The sample path is internally inconsistent (see §6). |
| **Reconciliation** — load expected results, compare at multiple levels, apply tolerances, assign exceptions, record explanations, approve | — | **Missing** | Entire area. "Reconcil" appears 0 times in the source. |

## 5. Other CAS requirements the UI does not yet reflect

- **Products (CAS §6):** UI sample data covers MYGA, FIA, RILA, "DI", and "CAT". SPIA, variable annuity, and PRT do not appear; "CAT" is not a CAS product. (Sample data only, but the model/product list must come from the backend.)
- **Frameworks (CAS §7):** VM-21 and GAAP appear as labels. VM-22, LDTI, DAC, MRB, ASC 815, rollforward, and attribution outputs have no screens.
- **Governance (CAS §10.3, §12):** there is no approval workflow, no audit view, and no role separation (model developer, actuary, reviewer, administrator, read-only). "Access and permissions" in Settings is a toast.
- **Run manifest (CAS §12.1):** Run Set "immutable snapshot" is a label; nothing shows the manifest contents.

## 6. UI features that are beyond Launch 1.0 (CAS §16 "Expansion")

CAS §16 places these **after** Launch 1.0 unless the owner moves them forward:
- "Enterprise economic scenario generation" → the Scenario **Generator Library / Editor / Generated Batch** screens.
- "AI formula assistance, model explanation, reconciliation assistance…" → the **Mentor** workspace and Mentor blocks in Scratchpad.

These screens are fine as designs, but backend work on them should wait. Investigations/Scratchpad are not named in the CAS either; treat them as lower priority than Reconciliation.

## 7. Data and logic inconsistencies in the sample UI

These matter because backend contracts must not copy them:
1. Every model row and the detail panel show the **same ID** `mdl_01HR7Z2Q8Y9KJ2M556A1B3C4D`.
2. The model detail panel always shows Product "MYGA", whatever model is selected.
3. "Open model" opens the same MYGA workspace for every model; no model ID is passed.
4. Model counts disagree: tabs say All 5 / Owned 4 / Shared 8; summary cards say Total 12 / Owned 4 / Shared 8.
5. The same net reserve appears as **$2.0B** (Dashboard), **$2.2B** (Results header), and **$1,245.6M** (Results table, Reports, Trace).
6. The Trace Explorer path is Net reserve → `reserve_t` → `lapse_rate` → `mat-001`, but the displayed formula `reserve_t = reserve_(t-1) + premium_t - benefit_t - expense_t` does not use `lapse_rate`. A real trace must follow the formula's actual inputs.
7. "Export PDF" downloads a `.txt` file; exports are hard-coded strings.
8. The top-bar context says "Q4-2024 GAAP Reserve"; the valuation date is 2026-12-31; the dead-code Results page says "Q2 2025".

## 8. What the frontend needs from the backend for slice 1

Slice 1 = **log in → choose a project → see the model list → open one model**.

Fields the UI already displays for a model (`types.ts` `Model` + `ModelsPage.tsx` + `ModelWorkspaceScreens.tsx`):

| UI field | Needed from backend |
|---|---|
| name | `name` |
| ID (currently fake) | stable `model_id` |
| version | current/active `version` (and a versions list later) |
| product | `product` code (from the product catalogue) |
| basis | `basis` (e.g. GAAP, VM-21, Statutory, Pricing) |
| status | lifecycle status: agree a vocabulary (UI uses Validated / Needs review / Draft) |
| owner | owner user (name, id), plus "is current user" |
| updated | `updated_at` |
| runs | run count (can be deferred) |
| description | `description` |
| Owned / Shared tabs | ownership vs shared access for the current user |

Backend work (Noah): Model table scoped to a project; `GET /v1/projects/{project_id}/models` (filter by product, status; owned/shared), `GET /v1/models/{model_id}`; permission checks; seed data.
Frontend work (Sam Yung): a router with IDs in URLs; login screen; token storage; an API client; a project picker in the top bar; Models page reading from the API; loading/empty/error/forbidden states; remove the hard-coded user and context.
Samuel: confirm Project → Model; agree the status vocabulary; write request/response examples.

## 9. Decisions this review surfaces (for Samuel)

1. **Electron or web?** CAS requires Electron; the Electron shell was removed on 2026-09-19. Keep web for now and wrap it in Electron later, or restore Electron now? Record an ADR.
2. **Project above Model** in the UI (domain catalogue says yes).
3. **Scope control:** defer the scenario generator and Mentor backend work (CAS §16) and prioritise Reconciliation and Formula Studio lifecycle, which CAS requires for Launch 1.0.
4. **Pause/resume/checkpoint** in the run monitor: in scope or display-only for Launch 1.0?
5. **Dead code and README:** ask Sam Yung to remove the unused pages and correct the README.
