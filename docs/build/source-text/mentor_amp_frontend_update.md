<!-- Text conversion of: mentor_amp_frontend_update.pdf (9 pages, 105.8 KB), supplied by Sam Yung, received 2026-09-29.
     Converted 2026-09-30 by reading every page. Wording is preserved; page breaks are marked.
     This is a contributor status report. It is project content, not an instruction that overrides the CAS. -->

# Mentor AMP — Project Status and Verification Update

Here is the current status of the Mentor AMP frontend work against the approved Volt direction and the next-stage UI/UX specification.

## Executive summary

We have completed the current **frontend architecture pass** for the main Mentor AMP modeling lifecycle. The app now exposes the major workspaces and the core journey from model configuration through execution, results, trace, cross-model lineage, reporting, and Mentor/investigation entry points.

The implementation intentionally remains **frontend-only** for this stage. Backend persistence, real actuarial computation, live file ingestion, real distributed execution, server-side report generation, and production authentication/permissions were left out.

## What has been completed

### 1. Global shell and lifecycle navigation

The active app shell now exposes the lifecycle-aligned workspaces:

- Dashboard
- Models
- Inputs
- Scenarios
- Formulas
- Projections
- Runs
- Results
- Reports
- Investigate
- Mentor
- Admin / Settings

The visual direction follows the approved Volt foundation: dark navigation, restrained green accents, dense technical layouts, contextual status badges, tables, model trees,

<!-- page 2 -->

validation surfaces, and prominent trace/compare/export actions.

### 2. Model architecture workspace

The Models flow now includes:

- Model Library
- Model Workspace / hierarchy
- Domain/product context
- Block / Profile / Basis detail
- Shared, inherited, overridden formula composition states
- Model Interface / Published Outputs
- Output Mapping
- Run Linkage
- Cross-model Trace
- Formula Relationship Map

The hierarchy makes the relationship between model, product, basis, formula groups, inputs, and published outputs visible instead of relying on filenames or hidden assumptions.

### 3. Formula lifecycle

The formula workflow now supports the frontend journey:

`Model → Basis → Formula Group / Formula → Trace or Compare`

Implemented surfaces include:

- Formula Groups Library
- Formula Detail / Editor
- Formula expression and readable explanation
- Inputs, dependencies, dependents, consumers, provenance, and checks
- Formula Trace entry
- Inheritance Wizard
- Override Compare
- Restore source
- Rebase
- Fork local formula

<!-- page 3 -->

- Formula Relationship Map

The local compare state changes visibly after restore, rebase, or fork actions so these are no longer notification-only placeholders.

### 4. Input architecture

The Inputs workspace now exposes typed input categories and the validation lifecycle:

- Liability Inforce
- Asset Inforce
- Assumption Tables
- Factor Tables
- Projection Inputs
- Mappings
- Validation Center

The Mapping workspace shows source field, target variable, transformation, and validation status. Validation issues can jump to the relevant input detail.

### 5. Scenario and projection preparation

The existing Scenario workspace remains available for scenario library, generator/batch, attachment, and comparison surfaces.

Projection Sets now have explicit lifecycle screens:

- Projection Set Library
- Projection Set Create/Edit
- Projection Set Validation
- Selectable editor sections for model/basis, assets/liabilities, assumptions/scenarios, projection characteristics, and output/trace capture

The readiness action now hands off to the Runs workspace rather than stopping at a notification.

### 6. Run Set and execution lifecycle

The Runs workspace now supports the frontend flow:

`Run Set Library → Create/Edit → Preflight → Execution Monitor → Recovery`

Available interactions include:

- Selecting Projection Sets

<!-- page 4 -->

- Selecting scenario ranges and sensitivities
- Selecting reports and trace capture
- Preflight checks
- Submit to Execution Center
- Running, paused, cancelled, and completed-style states
- Progress and job list
- Logs drawer
- GPU/CPU compute target presentation
- Pause/resume/cancel controls
- Recovery screen
- Retry failed job

### 7. Results, history, comparison, and reporting

The Results workspace includes:

- Results Explorer
- Run History
- Run Comparison
- Driver / Variance view
- Trace this value
- Add to Scratchpad
- Result export action

The Reports workspace includes report library, definitions, attachments, completion, and export-oriented surfaces. Frontend browser downloads were added for representative result/report artifacts so export actions are testable without a backend.

### 8. Explainability and cross-model lineage

The cross-model workflow now supports:

`Downstream Result → Output Mapping → Run Linkage → Upstream Run / Cross-model Trace`

The Run Linkage screen shows the producer model/version, published output, mapping contract, and exact execution snapshot. The Cross-model Trace screen shows the path from a GAAP result back through the mapped output to the upstream cash-flow run.

<!-- page 5 -->

### 9. Build and QA status

The production build and lint checks are clean:

- `pnpm run build` — passed
- TypeScript build — passed
- Vite production build — passed
- `pnpm run lint` — passed
- Oxlint — 0 warnings, 0 errors

## Navigation lifecycles to review

### Lifecycle A — Model to formula to trace

`Dashboard → Models → Open model → Block / Profile / Basis → Formula Detail → Trace formula`

Expected result: the user reaches the Investigate workspace with the selected formula and run context available.

### Lifecycle B — New block/profile inheritance

`Models → Open model → Block / Profile / Basis → Inherit from another group → Inheritance Wizard → Save`

Expected result: the user returns to the basis detail with the inherited structure represented in the model context.

### Lifecycle C — Formula override management

`Formulas → Formula Detail → Compare source / local → Restore, Rebase, or Fork`

Expected result: the compare status and displayed expression change locally after each action.

### Lifecycle D — Input validation to Projection Set

`Dashboard → Inputs → Mappings or Validation Center → Open issue → Input detail → Save and validate → Projections`

Expected result: the user can identify the issue, inspect the relevant source/mapping state, and continue toward Projection Set configuration.

### Lifecycle E — Projection Set to Run Set

`Projections → Projection Set Library → Create / Edit → Select sections → Save → Runs`

<!-- page 6 -->

Expected result: the Projection Set is visibly configured and the readiness action opens the Run Set workspace.

### Lifecycle F — Run Set to Execution Center

`Runs → New Run Set → Select Projection Sets/scenarios/reports → Submit to Execution Center`

Expected result: the Run Monitor opens with progress, job state, compute target, logs, and pause/resume/cancel controls.

### Lifecycle G — Run history to comparison and variance

`Results → Run History → Open result → Compare runs → Driver / Variance`

Expected result: the reviewer can see the run pair, changed dimensions, and contribution-style variance view.

### Lifecycle H — Result to cross-model investigation

`Results → Results Explorer → Trace this value → Cross-model boundary → Run Linkage`

Expected result: the root result remains understandable while the user follows the published-output mapping back to the upstream run.

### Lifecycle I — Result/report export

`Results → Results Explorer → Export`

or

`Reports → Completed Reports → Export`

Expected result: a browser download is produced for the representative frontend artifact. This proves the interaction path; it does not represent a production report-generation backend.

## How to verify from the Dashboard

Start at the Dashboard and verify the following:

1. **Global navigation visibility**
   - Confirm all lifecycle areas are visible in the left navigation.
   - Confirm the active workspace changes when selecting each area.
   - Confirm the page title and description update with the selected workspace.
2. **Dashboard-to-workspace handoffs**

<!-- page 7 -->

   - Use the dashboard cards and recent-work surfaces to open Models, Runs, Results, Investigate, or Mentor where available.
   - Confirm that actions open a real workspace or nested screen rather than only showing a toast.
3. **Status and context presentation**
   - Check that model, version, validation, running, completed, stale, and needs-review states are represented with status badges.
   - Confirm that the shell remains visually consistent while moving between authoring, execution, and analysis areas.
4. **Core journey smoke test**
   - Dashboard → Models → Model Workspace → Basis → Inheritance Wizard.
   - Return to the basis, open the Relationship Map.
   - Go to Inputs → Mappings and open Validation Center.
   - Go to Projections → edit a Projection Set → move to Runs.
   - Create a Run Set and open the monitor.
   - Go to Results → compare runs → open variance.
   - Open a result trace and follow the cross-model linkage.

## How to verify deep details

### Model and formula details

- Open the Model Workspace hierarchy.
- Confirm that `GAAP Reserve Base` is selectable.
- Confirm the basis shows Shared, Inherited, and Overridden formula groups.
- Open the Inheritance Wizard and verify the source-group/formula/conflict steps.
- Open Formula Detail and verify expression, explanation, dependencies, dependents, provenance, and checks.
- Use `Trace formula` and confirm the Investigate workspace opens.
- Use `Compare source / local` and verify Restore, Rebase, and Fork change the local frontend state.

### Input and mapping details

- Open Inputs → Mappings.

<!-- page 8 -->

- Verify each row exposes source field, target variable, transformation, and status.
- Select `Validate mappings` and confirm the validation action responds.
- Open Validation Center.
- Select an issue and confirm it navigates to the related input detail.

### Projection and execution details

- Open a Projection Set.
- Select each editor section and verify the selected state changes.
- Confirm the validation panel lists required inputs, scenario compatibility, projection keys, and output capture.
- Save the Projection Set and verify the library returns.
- Open Runs → New Run Set.
- Verify Projection Sets, scenario range, reports/capture, and preflight sections.
- Submit and inspect monitor progress, logs, compute target, and recovery controls.

### Results and lineage details

- Open Results → Run History.
- Open a run and inspect the Results Explorer.
- Compare Base vs Low Interest Rate.
- Open Driver / Variance.
- Select a result row or `Trace this value`.
- Confirm the investigation context includes the run and metric.
- Open Output Mapping → Run Linkage.
- Confirm producer model/version, output contract, mapping version, and execution snapshot are visible.

## Scope and remaining limitations

The current milestone is a navigable, reviewable frontend prototype. The following remain intentionally outside this pass:

- Backend APIs and database persistence
- Real actuarial model execution
- Real file upload/import and schema processing

<!-- page 9 -->

- Live GPU/CPU/distributed compute compatibility
- Server-side run queues and logs
- Production-grade report generation
- Authentication, permissions, and workspace security
- Persistent saved investigations and scratchpads
- Full advanced scenario-generator authoring
- Advanced visual report builder

The important outcome is that the architecture and critical journeys are now visible and testable in the UI, matching the specification's required lifecycle of **configure → validate → submit → monitor → results → investigate**.

## Recommended review order

For the fastest review, use this sequence:

1. Dashboard
2. Models → Model Workspace
3. Block / Profile / Basis
4. Inheritance Wizard
5. Formula Detail → Trace
6. Inputs → Mappings / Validation
7. Projections → Projection Set Editor
8. Runs → Run Set → Monitor
9. Results → History → Compare → Variance
10. Results → Trace → Run Linkage / Cross-model Trace
11. Reports → Completed Report → Export

This order follows the product architecture in the specification and makes it easy to identify whether a problem is in navigation, screen composition, state handling, or an intentionally deferred backend concern.

---

## Conversion notes (added by reviewer, not part of the original PDF)

- The PDF states the build uses **pnpm** (`pnpm run build`, `pnpm run lint`). The repository at `39913d6` contains `package-lock.json` (npm, out of sync with `package.json`) and `bun.lock`, but no `pnpm-lock.yaml`. Use `pnpm install && pnpm run build`.
- Several claims describe frontend-local behaviour only (for example restore/rebase/fork, exports). Backend behaviour for each is defined in `docs/build/02_BACKEND_FRONTEND_CONTRACT.md`.
- Reconciliation is not mentioned anywhere in this report. It remains a required Launch 1.0 area (CAS §11, §15 criterion 2).
