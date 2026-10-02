"""The immutable run package: the exact inputs one actuarial run executes against.

A run package is frozen when a run is submitted and never changes afterwards. Execution reads
the package — never the live, editable configuration tables — so a later edit to a formula,
variable, Projection Set, scenario or table cannot change what a submitted run calculates.

Document layout (``PACKAGE_SCHEMA_VERSION``)::

    {
      "schema_version": "mentoramp.run_package/v2",
      "fingerprint_algorithm": "sha256/canonical-json/v1",
      "fingerprint": "<sha256 of configuration>",          # the run_package_fingerprint
      "identity":   {...},   # package_id, frozen_for_run_id, run_set_id, project_id,
                             # created_at, created_by                       (NOT hashed)
      "governance": {...},   # lifecycle statuses and validation time at freeze (NOT hashed)
      "configuration": {     # everything that determines the calculation   (HASHED)
        "project": {"id"},
        "projection_set": {"id", "name", "version_label"},
        "model": {"id", "name", "product_code"},
        "model_version": {"id", "version_label", "basis", "methodology",
                          "methodology_version", "block_name", "profile_name", "illustrative"},
        "valuation_date": "YYYY-MM-DD", "horizon_months": int, "time_step": "monthly",
        "execution_backend": "cpu",
        "parameters": {...},                               # Projection Set parameters
        "formulas":  [{"id", "name", "output_variable", "function_ref", "version",
                       "dependencies", "expression_text", "unit", "illustrative",
                       "implementation": {"module", "qualname", "implementation_version",
                                          "implementation_fingerprint"},
                       "content_fingerprint"}],
        "variables": [{"id", "name", "display_name", "version", "kind", "data_type", "unit",
                       "required", "default_value", "source", "allow_scenario_override",
                       "definition_id", "content_fingerprint"}],   # model-version definitions
        "datasets": {
          "inforce":           [{"id", "name", "version_label", "record_count", "fingerprint",
                                 "fingerprint_scheme"}],
          "assumption_tables": [{"id", "name", "set_id", "set_name", "table_type",
                                 "version_label", "lookup_keys", "value_column", "row_count",
                                 "fingerprint"}],
          "factor_tables":     [same shape as assumption_tables],
        },
        "table_bindings": {"<table name>": {"kind": "assumption"|"factor", "table_id"}},
        "scenario": {"id", "set_id", "set_name", "name", "version_label", "scenario_type",
                     "as_of_date", "path_count", "fingerprint", "overrides": [...]},
        "outputs": {"output_variables": [...], "trace_scope": {"mode", "policy_ids"},
                    "max_traced_policies": int,
                    "published": {"<output>": {"display_name", "unit", "dimension",
                                               "aggregation"}}},
        "build": {"app_version", "engine_version", "code_version", "build_fingerprint",
                  "source_commit", "source_dirty", "identity_source"},
      }
    }

Large inputs (policy records, table rows) are referenced by ID and fingerprint, not copied: the
loader must find exactly the recorded content or the run fails before any calculation.

Before execution a worker checks, in order: the envelope (``verify_envelope``: the package row,
the document identity and the run's index fields all agree), the configuration fingerprint
(``verify``), the build identity, every formula implementation fingerprint, and every dataset
fingerprint. The configuration fingerprint deliberately excludes the identity block, so two runs
with the same configuration share it; the envelope check is what binds a package to its run.

Schema history: v1 (Work Package 1) had no implementation identity, no model-version variable
definitions, no inforce fingerprint scheme and no build fingerprint; v1 packages are readable but
are not executed by this build.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from app.core.execution.fingerprints import FINGERPRINT_ALGORITHM, fingerprint
from app.core.projection_engine.engine import order_formulas
from app.core.projection_engine.run_data import (
    FormulaSpec,
    PolicyRecord,
    RunData,
    ScenarioSpec,
    TableSpec,
    VariableSpec,
)

PACKAGE_SCHEMA_VERSION = "mentoramp.run_package/v2"
SUPPORTED_SCHEMA_VERSIONS = frozenset({PACKAGE_SCHEMA_VERSION})


class RunPackageError(Exception):
    """The package is missing, altered, unsupported or cannot be executed by this build."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def content_fingerprint(entry: Mapping[str, Any]) -> str:
    """Fingerprint of one formula or variable entry (excluding its own content_fingerprint)."""
    return fingerprint({key: value for key, value in entry.items() if key != "content_fingerprint"})


def seal(identity: dict, governance: dict, configuration: dict) -> dict[str, Any]:
    """Assemble a package document; its fingerprint covers the configuration only."""
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "fingerprint": fingerprint(configuration),
        "identity": identity,
        "governance": governance,
        "configuration": configuration,
    }


def verify(package: Mapping[str, Any], expected_fingerprint: str | None) -> dict[str, Any]:
    """Check schema and integrity; return the configuration or raise ``RunPackageError``."""
    schema = package.get("schema_version")
    if schema not in SUPPORTED_SCHEMA_VERSIONS:
        raise RunPackageError(
            "PACKAGE_SCHEMA_UNSUPPORTED",
            f"Run package schema '{schema}' is not supported by this build.",
            {"schema_version": schema},
        )
    if package.get("fingerprint_algorithm") != FINGERPRINT_ALGORITHM:
        raise RunPackageError(
            "PACKAGE_SCHEMA_UNSUPPORTED",
            f"Fingerprint algorithm '{package.get('fingerprint_algorithm')}' is not supported.",
        )
    configuration = package.get("configuration")
    if not isinstance(configuration, dict):
        raise RunPackageError("PACKAGE_TAMPERED", "The run package has no configuration.")
    actual = fingerprint(configuration)
    recorded = package.get("fingerprint")
    if actual != recorded or (expected_fingerprint is not None and actual != expected_fingerprint):
        raise RunPackageError(
            "PACKAGE_TAMPERED",
            "The run package does not match its recorded fingerprint; it was altered after "
            "submission.",
            {"recorded": recorded, "expected": expected_fingerprint, "actual": actual},
        )
    return configuration


def verify_envelope(
    document: Mapping[str, Any], package_row: Mapping[str, Any], run: Mapping[str, Any]
) -> None:
    """The package row, the package document and the run must describe the same run.

    ``package_row``: id, run_id, project_id, fingerprint, schema_version, fingerprint_algorithm.
    ``run``: id, project_id, run_set_id, run_package_fingerprint, scenario_id, model_version_id,
    projection_set_id, horizon_months, valuation_date (ISO text).
    Raises ``PACKAGE_IDENTITY_MISMATCH`` (frozen evidence relabelled) or ``RUN_INDEX_MISMATCH``
    (the mutable run row disagrees with the frozen package).
    """
    identity = document.get("identity") or {}
    configuration = document.get("configuration") or {}
    identity_checks = {
        "package_id": (package_row.get("id"), identity.get("package_id")),
        "run_id": (package_row.get("run_id"), identity.get("frozen_for_run_id"), run.get("id")),
        "project_id": (package_row.get("project_id"), identity.get("project_id"), run.get("project_id"),
                       (configuration.get("project") or {}).get("id")),
        "run_set_id": (identity.get("run_set_id"), run.get("run_set_id")),
        "fingerprint": (package_row.get("fingerprint"), document.get("fingerprint"),
                        run.get("run_package_fingerprint")),
        "schema_version": (package_row.get("schema_version"), document.get("schema_version")),
        "fingerprint_algorithm": (package_row.get("fingerprint_algorithm"),
                                  document.get("fingerprint_algorithm")),
    }
    mismatched = {name: list(values) for name, values in identity_checks.items() if len(set(values)) != 1}
    if mismatched:
        raise RunPackageError(
            "PACKAGE_IDENTITY_MISMATCH",
            f"The run package does not belong to this run: {', '.join(sorted(mismatched))} disagree.",
            {"mismatched": mismatched},
        )
    index_checks = {
        "scenario_id": ((configuration.get("scenario") or {}).get("id"), run.get("scenario_id")),
        "model_version_id": ((configuration.get("model_version") or {}).get("id"), run.get("model_version_id")),
        "projection_set_id": ((configuration.get("projection_set") or {}).get("id"), run.get("projection_set_id")),
        "horizon_months": (configuration.get("horizon_months"), run.get("horizon_months")),
        "valuation_date": (configuration.get("valuation_date"), run.get("valuation_date")),
    }
    mismatched = {name: list(values) for name, values in index_checks.items() if values[0] != values[1]}
    if mismatched:
        raise RunPackageError(
            "RUN_INDEX_MISMATCH",
            f"The run row disagrees with its frozen package: {', '.join(sorted(mismatched))}.",
            {"mismatched": mismatched},
        )


def formula_spec(entry: Mapping[str, Any]) -> FormulaSpec:
    return FormulaSpec(
        id=entry["id"],
        name=entry["name"],
        output_variable=entry["output_variable"],
        function_ref=entry["function_ref"],
        dependencies=tuple(entry.get("dependencies") or ()),
        expression_text=entry.get("expression_text"),
        version=entry.get("version") or "v1",
        illustrative=bool(entry.get("illustrative")),
    )


def variable_spec(entry: Mapping[str, Any]) -> VariableSpec:
    return VariableSpec(
        name=entry["name"],
        kind=entry["kind"],
        data_type=entry.get("data_type") or "number",
        unit=entry.get("unit"),
        source=dict(entry.get("source") or {}),
        default_value=entry.get("default_value"),
        required=bool(entry.get("required", True)),
        display_name=entry.get("display_name"),
        version=entry.get("version") or "v1",
        allow_scenario_override=bool(entry.get("allow_scenario_override")),
    )


def traced_policy_ids(
    trace_scope: Mapping[str, Any] | None, policy_ids: Iterable[str], limit: int
) -> frozenset[str]:
    """Policies whose calculation trace is captured, capped at ``limit`` (recorded in the package)."""
    scope = trace_scope or {}
    mode = scope.get("mode", "none")
    available = list(policy_ids)
    if mode == "all":
        return frozenset(available[:limit])
    if mode == "selected_policies":
        wanted = set(scope.get("policy_ids") or [])
        return frozenset([policy_id for policy_id in available if policy_id in wanted][:limit])
    return frozenset()


def function_refs(configuration: Mapping[str, Any]) -> list[str]:
    return sorted({entry["function_ref"] for entry in configuration.get("formulas") or []})


def build_run_data(
    configuration: Mapping[str, Any],
    policies: list[PolicyRecord],
    tables: dict[str, TableSpec],
) -> RunData:
    """Engine inputs from a verified package plus its fingerprint-verified datasets."""
    formulas = order_formulas([formula_spec(entry) for entry in configuration["formulas"]])
    variables = {entry["name"]: variable_spec(entry) for entry in configuration["variables"]}
    scenario = configuration.get("scenario") or {}
    outputs = configuration.get("outputs") or {}
    return RunData(
        variables=variables,
        formulas=formulas,
        tables=tables,
        policies=policies,
        scenario=ScenarioSpec(
            id=scenario.get("id"),
            name=scenario.get("name") or "No scenario",
            overrides=tuple(dict(item) for item in scenario.get("overrides") or ()),
        ),
        valuation_date=date.fromisoformat(configuration["valuation_date"]),
        horizon_months=int(configuration["horizon_months"]),
        output_variables=list(outputs.get("output_variables") or []),
        traced_policy_ids=traced_policy_ids(
            outputs.get("trace_scope"),
            [policy.policy_id for policy in policies],
            int(outputs.get("max_traced_policies") or 0),
        ),
        parameters=dict(configuration.get("parameters") or {}),
    )
