# MentorAmp UI Submission Review — Sam Yung

**Review date:** 2026-09-23  
**Reviewer:** Samuel Ayinla — Technical Lead / Core Backend  
**Submission:** `mentor-amp-core-for_approval.zip`  
**Archive SHA-256:** `9740BE219B09AC507A8F438FA55291159D8A4542EB52EBDC16F6D4D2C3F21107`  
**Authority:** Launch Release 1.0 Consolidated Architecture and Delivery Specification (`CAS`), supported by the Master Plan (`MP`), Week 0 Readiness Plan (`W0R`), and Independent Team Task Document (`ITD`)  
**Review type:** Visual design and workflow-coverage review; the archive contains PNG files only

## 1. Decision

**Proceed with this submission as the visual and interaction design foundation, subject to required revisions.**

Do not discard the work. The submission establishes a credible visual language, a strong trace experience, useful run states, scenario comparison, production-versus-experiment separation, and thoughtful accessibility guidance.

Do not yet approve it as the complete Launch 1.0 application or as implemented frontend functionality. The archive contains no Figma source, React/TypeScript source, Electron package, tests, API integration, or interactive prototype. Several mandatory workflows are also missing or partial.

In simple terms: the drawings for important rooms are good, but the complete building plan is not yet present, and the plumbing to the backend cannot be tested from pictures.

## 2. Evidence reviewed

The archive contains 16 PNG files and no code or design-source files.

| File | Intended area | Review result |
|---|---|---|
| `01-foundations.png` | Visual foundations | Keep; resolve brand and typography inconsistencies. |
| `03-shell-navigation.png` | Main shell/dashboard/provenance | Keep and extend; clarify workspace functions and provenance semantics. |
| `06-model-library.png` | Model inventory | Keep; add governance and lifecycle actions. |
| `07-model-workspace.png` | Model/object workspace | Keep with data-source corrections. |
| `08-formula-intelligence.png` | Formula explanation/investigation | Keep as an investigation view; it is not the required Formula Studio lifecycle. |
| `10-scenario-comparison.png` | Run/scenario comparison | Keep after resolving contradictory comparison states. |
| `11-contextual-mentor.png` | Contextual assistance | Useful later; require clear evidence, permissions, and AI guardrails. |
| `12-data-assumptions.png` | Assumption inspection | Keep as one Data Manager sub-view; add the complete ingestion lifecycle. |
| `13-run-management.png` | Run configuration and monitoring | Keep after adding required run dimensions and separating states clearly. |
| `14-state-matrix.png` | States and accessibility | Strong design evidence; must be implemented and tested per screen. |
| `15-dark-theme-parity.png` | Dark theme/trace | Keep as secondary theme evidence; verify contrast and design-token parity. |
| `16-saved-investigation.png` | Saved investigations | Useful additional capability after the seven required areas are covered. |
| `17-trace-explorer-states.png` | Trace explorer | Strongest requirement-aligned screen; retain and connect to trace contracts. |
| `18-scratchpad-lifecycle.png` | Controlled experiments | Keep; align promotion and approval with governance lifecycle. |
| `19-results-explorer-corrected.png` | Results explorer | Good foundation; add policy detail, attribution, rollforwards, disclosures, comparison, and governed export. |
| `20-component-completeness.png` | Reusable component states | Strong design foundation; verify real keyboard, screen-reader, responsive, and error behavior. |

Files numbered `02`, `04`, `05`, and `09` were not present. Confirm whether those files were intentionally removed or accidentally omitted.

## 3. What is already strong

### 3.1 Visual hierarchy

The screens are calm, professional, and suitable for a dense analytical product. Tables, inspectors, context bars, badges, formula tokens, and technical identifiers are visually distinct.

### 3.2 Trace and provenance concept

The trace explorer shows a useful chain:

`result → breakdown → formula → variable → source table → exact value`

That directly supports CAS §5 and §11. The list alternative for a graph is also a good accessibility and large-trace decision.

### 3.3 Production versus experimentation

The locked production baseline and editable scratchpad communicate an important control. A user can understand that changing an assumption in an experiment does not silently alter production.

Example:

- Production interest rate: `4.25%`, locked.
- Experiment interest rate: `5.00%`, editable.
- The UI shows the resulting reserve difference before anything is promoted.

### 3.4 Operational states

Running, queued, failed, loading, empty, stale, missing dependency, circular reference, no permission, and large-trace states are represented. The designs generally use an icon and text as well as color.

### 3.5 Scenario comparison

Baseline/alternative selection, changed inputs, differences, percentage changes, and an impact chart form a useful comparison foundation.

### 3.6 Reusable component thinking

The foundations and component sheets provide enough direction to begin a proper design system, subject to token cleanup and implementation testing.

## 4. Coverage against the seven mandatory application areas

| Requirement | Evidence in submission | Assessment | Required addition before design approval |
|---|---|---|---|
| `LR1-APP-001` Workspace | Dashboard, model library, model workspace, recent runs | **Partial** | Project creation/selection, blocks, product configuration, valuation-date management, model-version administration, users/roles, and clear workspace navigation. |
| `LR1-APP-002` Data Manager | Assumption table and scenario-file list | **Partial** | Import, source selection, mapping, validation results, rejected records, preview, compare versions, approve, and manage inforce, factors, assets, actuals, expected results, prior outputs, CSV/TSV/Excel/Parquet/DB extracts. |
| `LR1-APP-003` Formula Studio | Formula display, variable investigation, dependency link | **Partial** | Search, create, edit, document, syntax/variable validation, test cases, version comparison, submit, peer review, approve, activate, rollback, ownership and effective dates. |
| `LR1-APP-004` Run Center | Configure/validate/submit/monitor concept | **Partial** | Basis, methodology, projection key, exact data versions, factor/scenario sets, output package, trace level, execution backend, approval state, warnings, manifest preview, cancellation rules, retry/restart. |
| `LR1-APP-005` Results Explorer | Aggregate cards, time series, table, download, scenario comparison | **Partial** | Policy/segment/cohort filters, run/version comparison, reserve components, attribution, rollforwards, disclosures, methodology/basis context, reconciliation status, approval status, and governed export package. |
| `LR1-APP-006` Trace & Debug | Trace graph/list, source and exact-value nodes, errors/states | **Strong partial** | Intermediate calculation values, policy/scenario/time selection, error details, formula/input versions, overrides, trace levels, pagination/filtering, and live source-record access. |
| `LR1-APP-007` Reconciliation | No dedicated reconciliation workflow | **Missing / blocked** | Load expected results, choose comparison level, show expected/actual/difference, apply tolerance, assign exceptions, record explanations, retest, approve completion, retain evidence. |

### Simple example of the missing reconciliation screen

Suppose MentorAmp calculates a reserve of `$1,000,004` and the approved expected result is `$1,000,000` with a tolerance of `$10`.

The screen should show:

| Expected | Actual | Difference | Tolerance | Result |
|---:|---:|---:|---:|---|
| $1,000,000 | $1,000,004 | $4 | $10 | Pass |

If the difference is `$40`, the screen should create an exception that a reviewer can assign, explain, retest, and approve. None of the submitted screens currently completes this workflow.

## 5. Required changes by area

### 5.1 Workspace

Keep the dashboard and model library, then add:

- project and block creation/selection;
- product and reference-configuration selection;
- valuation-date and model-version management;
- model owner, reviewer, approval state, effective date, and change reason;
- user/role administration or a clear route to it;
- recent imports, runs, reconciliation items, and approvals requiring action.

The current dashboard is result-oriented. A workspace must also let a user establish the controlled context in which that result will be created.

### 5.2 Data Manager

The assumption table is a useful inspection screen, but Data Manager must start earlier in the lifecycle.

Required flow:

`upload/select source → choose import definition → map fields → preview → validate → correct/reject → version → approve → make available to a run`

Example: a CSV field called `Pol_ID` should be visibly mapped to canonical `policy_id`. Invalid dates and duplicate IDs should appear as rejected records with downloadable evidence. The approved dataset version and fingerprint should then be available to Run Center.

Add views for:

- inforce data;
- assumptions;
- factors;
- scenarios;
- assets/supporting assets;
- actual experience;
- independently prepared expected results;
- prior-run and derived datasets.

### 5.3 Formula Studio

Rename or position `Formula Intelligence` as a formula investigation/explanation screen. It does not replace Formula Studio.

Formula Studio needs at least:

- formula search and inventory;
- create/edit with controlled expression syntax;
- semantic variable browser;
- dependency graph and impacted calculations;
- syntax, type, unit, and missing-symbol validation;
- test-case input and expected/actual output;
- version diff;
- documentation, owner, applicability, effective date, and change reason;
- submit, review, approve, activate, retire, and rollback actions;
- read-only production versions and editable draft versions.

Example: changing `net_cash_flow = premium - benefit - expense` should create a draft version. It should not change production until tests pass and an authorized reviewer approves and activates it.

### 5.4 Run Center

The five-step pattern is appropriate. The configuration must capture the complete immutable run definition.

Add or make explicit:

- project and block;
- product and product-configuration version;
- valuation basis and methodology;
- projection key and valuation date;
- inforce, assumption, factor, scenario, asset, and prior-output versions;
- projection horizon and frequency;
- output package and output granularity;
- trace level;
- CPU, PySpark, or GPU execution backend;
- validation warnings and blocking errors;
- manifest preview before submission;
- authorized cancellation, retry, restart, and rerun behavior.

The current screen mixes configuration for one run with monitoring cards for other runs. Separate these into clearly named panels or pages so a user does not confuse the run being configured with the run being monitored.

### 5.5 Results Explorer

Retain the metric cards, chart, table, units, precision, rounding, and trace entry point. Add:

- policy, block, product, cohort, segment, scenario, time, basis, and methodology filters where applicable;
- policy-level and aggregate drill-down;
- comparison by run, model version, formula version, assumption version, and period;
- reserve component breakdown;
- attribution and configurable rollforward views;
- beginning balance, movements, and ending-balance reconciliation;
- disclosure-supporting outputs;
- reconciliation status and unresolved exceptions;
- approval status and approved-export action;
- export contents, format, data classification, and audit event.

A generic result table can serve all products, but the selected product and methodology determine which dimensions and components are available.

### 5.6 Trace & Debug

This is the strongest area. Preserve the graph/list toggle and selected-node inspector.

Correct and extend it so that:

- a result points first to its calculation/formula or aggregate components, not to one assumption table as if that table alone produced the result;
- every node carries version and effective-date context;
- exact lookup keys and the source record are available;
- intermediate input/output values appear at each formula step;
- overrides and default use are visible;
- missing, invalid, circular, permission, and unavailable-source errors have diagnostic details;
- large traces use server-side filtering/pagination rather than attempting to render the entire graph;
- selected policy, scenario, projection time, basis, and methodology remain visible.

### 5.7 Reconciliation

Create this as a first-class application area. It cannot be represented only by a badge in Results.

Required states:

- expected-results package not loaded;
- validating expected-results format;
- comparison ready;
- within tolerance;
- outside tolerance;
- exception unassigned/assigned;
- explanation recorded;
- rerun required;
- ready for approval;
- approved/locked;
- reopened with an audit reason.

## 6. Data and actuarial meaning corrections

These issues should be corrected before the screens are used as backend/API requirements.

### 6.1 A calculated result must not appear to come from one assumption table

In `07-model-workspace.png`, Net reserve is shown with source `mat-001 Lapse Rates by Duration × Product`, even though the formula preview has several dependencies. The assumption table is one input source, not the source of the final reserve.

Use language such as:

- **Calculated by:** formula/version;
- **Direct dependencies:** four inputs/results;
- **Selected input source:** `mat-001` for `lapse_rate`;
- **Run context:** run, model, scenario, time, policy/aggregate level.

### 6.2 Formula examples are illustrative until actuarially approved

The reserve equation and cause-and-effect descriptions shown in the images must be labeled as illustrative or synthetic until the Actuarial Product Owner approves the formula intent and expected results.

The UI must display backend-provided formula definitions, values, units, and trace evidence. React code must not calculate or invent the authoritative reserve.

### 6.3 Scenario comparison contains a contradiction

`10-scenario-comparison.png` shows `Incompatible period or run` near the top, while the bottom says the same model version and period are directly comparable. Display only the state that matches the selected runs.

### 6.4 Dates and identifiers need a coherent sample story

Several screens combine names such as `Q4-2024`, run timestamps in 2024/2025, and valuation dates in 2026. Future valuation dates can be valid, but the sample narrative must make that intentional. Create one controlled demo dataset and reuse its IDs and dates across all screens.

### 6.5 Contextual Mentor claims need evidence labels

Separate:

- **Model fact:** directly retrieved from formula/run/trace data;
- **Derived explanation:** deterministic interpretation of evidence;
- **AI interpretation:** generated text requiring review;
- **Suggested next step:** non-authoritative workflow guidance.

The UI should never show an AI statement as an approved actuarial conclusion.

## 7. Governance, approval, and security gaps

Add designs for:

- formula approval and activation;
- dataset approval;
- model/product-configuration approval;
- reconciliation completion approval;
- result/run approval where required;
- audit history with user, timestamp, old/new version, action, and reason;
- run manifest inspection;
- export approval and audit evidence;
- roles for model developer, actuary, reviewer, administrator, and read-only user;
- access denied/request access flows tied to actual permissions;
- retention/deletion requests and their audit status where exposed in the application.

An `Audit Trail` tab is visible, but its content and workflow were not supplied.

## 8. Product, framework, and reporting coverage

The submitted examples concentrate on MYGA, FIA, RILA, and reserve analysis. The UI does not need a separate engine-shaped interface for every product, but it must prove that configuration and results can support the agreed reference slices for:

- SPIA;
- MYGA/fixed deferred annuity;
- FIA;
- RILA;
- IDI;
- variable annuity;
- PRT;
- future life-data readiness;
- VM-21, VM-22, LDTI, DAC, MRB, and ASC 815;
- GAAP rollforwards, attribution, and disclosure-supporting outputs.

Create at least one product-configuration pattern and one methodology/framework-selection pattern that can adapt based on backend metadata. Add a Reports/Disclosures design or remove the unexplained navigation item until its workflow is defined.

## 9. Design-system and accessibility review

### Retain

- visible focus state;
- icon plus text for status;
- list alternative for trace graphs;
- reduced-motion guidance;
- loading, empty, error, failed, stale, permission, and dependency states;
- tabular numerals for actuarial data;
- reusable buttons, inputs, tabs, badges, cells, trace nodes, and experiment cards.

### Resolve

- `01-foundations.png` specifies Manrope for interface type, while other sheets specify IBM Plex Sans. Select and document one interface-font system.
- Logo treatments vary between a bar mark and an `M` mark. Select a primary mark and permitted compact variants.
- Navigation differs by screen: Compare and Saved Investigations appear only in some states. Define one information architecture with role/feature-dependent rules.
- Verify WCAG contrast using actual token values in both themes; a screenshot cannot prove contrast.
- Define responsive desktop breakpoints and behavior for narrow Electron windows, inspectors, large tables, and graphs.
- Test keyboard order, focus trapping, Escape behavior, screen-reader names, live run-status announcements, table semantics, and zoom at implementation time.

## 10. Frontend-to-backend dependency map

This is the minimum contract direction. Exact endpoint paths remain subject to API design review.

| UI area | Required backend/service capability |
|---|---|
| Workspace | Projects, blocks, products, model versions, users/roles, recent activity, approvals. |
| Data Manager | Upload/import, preview, mappings, validation, rejected records, versioning, approval, fingerprints, import logs. |
| Formula Studio | Formula/variable registries, validation, dependency graph, test execution, version diff, lifecycle transitions. |
| Run Center | Run configuration validation, manifest preview, submit, status, warnings/errors, cancellation, retry/restart, logs. |
| Results Explorer | Result metadata, filters, aggregates, policy detail, comparisons, rollforwards, attribution, disclosures, export. |
| Trace & Debug | Trace search, path/graph/list, node details, source lookup, intermediate values, errors, pagination. |
| Reconciliation | Expected-result import, comparison, tolerances, exception assignment, explanations, rerun links, approval. |
| Governance | Audit events, approvals, permissions, manifests, retention/export events. |

Every screen must distinguish display calculations from authoritative calculations. Formatting `1,245,612,000` as `$1.246B` is a UI concern. Calculating the reserve itself belongs to the backend engine.

## 11. Priority action list for Sam Yung

### P0 — required before design freeze

1. Supply editable Figma/design source and identify the exact version reviewed.
2. Supply or link the frontend source/branch/commit if any of these screens are implemented.
3. Add the missing Reconciliation workflow.
4. Expand Formula Studio to the full create/test/version/approve/activate/rollback lifecycle.
5. Expand Data Manager to import/map/validate/version/approve all required data categories.
6. Add complete Run Center selections and manifest preview.
7. Expand Results Explorer for policy/aggregate filtering, rollforwards, attribution, disclosures, comparison, reconciliation status, and approved export.
8. Add Workspace project/block/version/user controls.
9. Correct provenance semantics, scenario-comparison contradiction, and sample date/ID consistency.
10. Map every action and state to a backend/API dependency with Samuel and Noah.

### P1 — required before implementation acceptance

1. Add lifecycle/approval/audit screens and role behavior.
2. Add product configuration and methodology/framework patterns.
3. Add run manifest, warnings, logs, retry/restart, and governed export details.
4. Define Reports/Disclosures navigation and screens.
5. Confirm responsive Electron behavior and all accessibility interactions.
6. Implement component tests, accessibility tests, and live API E2E tests.

### P2 — retain after the core journey is secured

1. Contextual Mentor.
2. Saved investigations.
3. Scratchpad enhancements.
4. Full dark-theme polish.

These P2 concepts are valuable, but they should not consume delivery time needed for a missing P0 workflow.

## 12. Resubmission acceptance checklist

- [ ] The seven mandatory application areas each have a complete happy path.
- [ ] Each area includes loading, empty, validation, failure, success, approval, and unavailable-backend states where applicable.
- [ ] Every visible action maps to an API/service capability or is clearly marked as future work.
- [ ] The UI performs no authoritative actuarial calculation.
- [ ] One coherent synthetic demo dataset supplies consistent dates, IDs, versions, products, runs, formulas, and expected results.
- [ ] Formula, data, model, run, reconciliation, and export approval lifecycles are visible.
- [ ] Reconciliation supports expected/actual/tolerance/exception/explanation/approval.
- [ ] Trace shows formula, variables, exact keys, sources, intermediate values, versions, and errors.
- [ ] Product/methodology configuration supports the Launch 1.0 reference-slice pattern.
- [ ] Brand, logo, typography, navigation, and tokens are consistent.
- [ ] Editable design source, frontend commit, build steps, and test evidence are supplied.
- [ ] Responsive, keyboard, screen-reader, contrast, and Electron behavior are tested rather than inferred from screenshots.

## 13. Technical-lead recommendation

Use these screens as the foundation for the `07_ui` work package and begin API-contract mapping with them. Ask Sam Yung to revise the P0 items before the visual design is frozen. Noah and Samuel should review each screen’s request, response, permissions, statuses, errors, and lifecycle transitions before frontend implementation proceeds deeply.

The current design earns **reuse/improve status as visual design evidence**. It does not yet earn `REUSE` as working frontend code because no working code was included. Missing mandatory workflows remain `BUILD` until supplied and verified.
