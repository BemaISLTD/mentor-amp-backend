"""Canonical JSON and SHA-256 fingerprints for configuration and datasets.

One algorithm, used everywhere a fingerprint is written or checked:

    fingerprint(value) = sha256( json.dumps(value, sort_keys=True, separators=(",", ":"),
                                            default=str) )

Datasets are fingerprinted as the canonical JSON array of their rows, in a documented order.
``fingerprint_sequence`` computes exactly the same digest as ``fingerprint(list(rows))`` but
streams the rows, so a large dataset never has to be held as one string.
"""

import hashlib
import json
from collections.abc import Iterable
from typing import Any

FINGERPRINT_ALGORITHM = "sha256/canonical-json/v1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    """SHA-256 (hex) of the canonical JSON form of ``value``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def fingerprint_sequence(items: Iterable[Any]) -> str:
    """Same digest as ``fingerprint(list(items))``, computed incrementally."""
    digest = hashlib.sha256(b"[")
    first = True
    for item in items:
        if not first:
            digest.update(b",")
        digest.update(canonical_json(item).encode("utf-8"))
        first = False
    digest.update(b"]")
    return digest.hexdigest()


# Inforce fingerprint schemes. v1 (Work Package 1) hashed only each record's data, ordered by
# (policy_id, load order); it did not cover the policy_id column the engine uses as identity.
# v2 hashes {"policy_id", "data"} per record, ordered by policy_id alone, and requires unique
# policy IDs, so neither identity nor storage order can change without changing the fingerprint.
INFORCE_FINGERPRINT_SCHEME = "inforce-v2"


class DuplicatePolicyIds(ValueError):
    def __init__(self, policy_ids: list[str]):
        super().__init__(f"Duplicate policy IDs in inforce data: {policy_ids[:5]}")
        self.policy_ids = policy_ids


def duplicate_policy_ids(policy_ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for policy_id in policy_ids:
        if policy_id in seen:
            duplicates.add(policy_id)
        seen.add(policy_id)
    return sorted(duplicates)


def inforce_fingerprint(records: Iterable[tuple[str, dict]]) -> str:
    """Scheme inforce-v2: canonical {"policy_id", "data"} records sorted by (unique) policy_id."""
    ordered = sorted(((str(policy_id), dict(data or {})) for policy_id, data in records), key=lambda item: item[0])
    duplicates = duplicate_policy_ids(policy_id for policy_id, _data in ordered)
    if duplicates:
        raise DuplicatePolicyIds(duplicates)
    return fingerprint_sequence({"policy_id": policy_id, "data": data} for policy_id, data in ordered)


def table_fingerprint(rows: Iterable[dict]) -> str:
    """Fingerprint of an assumption or factor table: its rows in stored order."""
    return fingerprint_sequence(rows)


def scenario_fingerprint(overrides: Iterable[dict]) -> str:
    """Fingerprint of a deterministic scenario: its override list in stored order."""
    return fingerprint(list(overrides))
