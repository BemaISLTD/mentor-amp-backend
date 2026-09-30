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


def inforce_fingerprint(records_in_order: Iterable[dict]) -> str:
    """Fingerprint of an inforce file: its record dicts ordered by (policy_id, load order)."""
    return fingerprint_sequence(records_in_order)


def table_fingerprint(rows: Iterable[dict]) -> str:
    """Fingerprint of an assumption or factor table: its rows in stored order."""
    return fingerprint_sequence(rows)


def scenario_fingerprint(overrides: Iterable[dict]) -> str:
    """Fingerprint of a deterministic scenario: its override list in stored order."""
    return fingerprint(list(overrides))
