"""Loads the datasets a run package references — by ID only — and proves they are unchanged.

For every inforce file and every bound assumption/factor table the loader recomputes the
fingerprint of the stored content and compares it with the fingerprint frozen in the package.
Any difference (edited rows, deleted dataset, different record count) raises
``DataIntegrityError`` before a single value is calculated.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.core.execution.fingerprints import (
    SUPPORTED_INFORCE_FINGERPRINT_SCHEMES,
    DuplicatePolicyIds,
    inforce_fingerprint,
    table_fingerprint,
)
from app.core.projection_engine.run_data import PolicyRecord, TableSpec
from app.db.models.assumption import AssumptionTable
from app.db.models.factor import FactorTable
from app.db.models.inforce import InforceFile, InforceRecord

TABLE_MODELS = {"assumption": AssumptionTable, "factor": FactorTable}
TABLE_SECTIONS = {"assumption": "assumption_tables", "factor": "factor_tables"}


class DataIntegrityError(Exception):
    """A dataset referenced by a run package is missing or no longer matches its fingerprint."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def load_policies(db: Session, configuration: dict[str, Any]) -> list[PolicyRecord]:
    policies: list[PolicyRecord] = []
    for entry in configuration["datasets"]["inforce"]:
        file = db.get(InforceFile, entry["id"])
        if file is None or file.project_id != configuration["project"]["id"]:
            raise DataIntegrityError(
                "DATASET_MISSING",
                f"Inforce file '{entry['name']}' ({entry['id']}) no longer exists.",
                {"dataset_id": entry["id"]},
            )
        if entry.get("fingerprint_scheme") not in SUPPORTED_INFORCE_FINGERPRINT_SCHEMES:
            raise DataIntegrityError(
                "DATASET_FINGERPRINT_SCHEME_UNSUPPORTED",
                f"Inforce file '{entry['name']}' was frozen with fingerprint scheme "
                f"'{entry.get('fingerprint_scheme')}'; this build verifies "
                f"{sorted(SUPPORTED_INFORCE_FINGERPRINT_SCHEMES)}.",
                {"dataset_id": entry["id"]},
            )
        # Canonical order is (unique) policy_id, sorted in Python: neither storage order nor the
        # database's text collation can change the fingerprint.
        records = sorted(
            ((row.policy_id, dict(row.data or {})) for row in db.query(InforceRecord.policy_id, InforceRecord.data)
             .filter(InforceRecord.file_id == entry["id"]).all()),
            key=lambda item: item[0],
        )
        try:
            actual = inforce_fingerprint(records)
        except DuplicatePolicyIds as error:
            raise DataIntegrityError(
                "DATASET_INVALID",
                f"Inforce file '{entry['name']}' contains duplicate policy IDs {error.policy_ids[:5]}.",
                {"dataset_id": entry["id"], "duplicate_policy_ids": error.policy_ids[:20]},
            ) from error
        if len(records) != entry["record_count"] or actual != entry["fingerprint"]:
            raise DataIntegrityError(
                "DATASET_FINGERPRINT_MISMATCH",
                f"Inforce file '{entry['name']}' changed after the run was submitted.",
                {
                    "dataset_id": entry["id"],
                    "expected_fingerprint": entry["fingerprint"],
                    "actual_fingerprint": actual,
                    "expected_record_count": entry["record_count"],
                    "actual_record_count": len(records),
                },
            )
        policies.extend(
            PolicyRecord(
                policy_id=policy_id,
                data=dict(data or {}),
                dataset_id=entry["id"],
                dataset_name=entry["name"],
            )
            for policy_id, data in records
        )
    return policies


def load_tables(db: Session, configuration: dict[str, Any]) -> dict[str, TableSpec]:
    """Every table bound in the package, keyed by the name the variables use."""
    entries = {
        (kind, entry["id"]): entry
        for kind, section in TABLE_SECTIONS.items()
        for entry in configuration["datasets"].get(section) or []
    }
    tables: dict[str, TableSpec] = {}
    for name, binding in sorted((configuration.get("table_bindings") or {}).items()):
        kind, table_id = binding["kind"], binding["table_id"]
        entry = entries.get((kind, table_id))
        if entry is None:
            raise DataIntegrityError(
                "PACKAGE_INCONSISTENT",
                f"Table binding '{name}' points to {kind} table {table_id}, which the package "
                "does not list.",
            )
        row = db.get(TABLE_MODELS[kind], table_id)
        if row is None:
            raise DataIntegrityError(
                "DATASET_MISSING",
                f"{kind.title()} table '{name}' ({table_id}) no longer exists.",
                {"dataset_id": table_id},
            )
        data = list(row.data or [])
        actual = table_fingerprint(data)
        if actual != entry["fingerprint"] or len(data) != entry["row_count"]:
            raise DataIntegrityError(
                "DATASET_FINGERPRINT_MISMATCH",
                f"{kind.title()} table '{name}' changed after the run was submitted.",
                {
                    "dataset_id": table_id,
                    "expected_fingerprint": entry["fingerprint"],
                    "actual_fingerprint": actual,
                    "expected_row_count": entry["row_count"],
                    "actual_row_count": len(data),
                },
            )
        # Lookup keys and value column come from the package, not from the (editable) row.
        table = TableSpec(
            id=table_id,
            name=name,
            key_columns=tuple(entry["lookup_keys"]),
            value_column=entry["value_column"],
            rows=data,
            fingerprint=entry["fingerprint"],
            kind=kind,
        )
        duplicates = table.build_index()
        if duplicates:
            raise DataIntegrityError(
                "DATASET_AMBIGUOUS",
                f"Table '{name}' has duplicate lookup keys {duplicates[:3]}.",
                {"dataset_id": table_id},
            )
        tables[name] = table
    return tables
