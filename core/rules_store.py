"""Persistent storage for the Input-mode bin rule master.

The default categories ship in ``config.BIN_RULES``.  The first time the store
is used it is seeded from that table into ``data/bin_rules.json``; every later
create / update / delete / reorder written through this module survives
restarts and is exactly what the analysis engine matches against.  The web UI
exposes these operations as the Bin Recommendation Rules CRUD endpoints.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from typing import Any, Dict, List, Optional

import config

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "bin_rules.json",
)

_lock = threading.Lock()
_cache: Dict[str, Any] = {"sig": None, "rules": None}

REQUIRED_FIELDS = ("name", "min_volume", "max_volume", "min_weight", "max_weight")


def set_store_path(path: str) -> str:
    """Point the store at a different file (tests use a temp file)."""
    global _PATH
    old = _PATH
    _PATH = path
    with _lock:
        _cache["sig"] = None
        _cache["rules"] = None
    return old


def _signature() -> Optional[str]:
    if not os.path.exists(_PATH):
        return None
    with open(_PATH, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _ensure_file() -> None:
    """Seed the store from ``config.BIN_RULES`` the first time it is used."""
    if os.path.exists(_PATH):
        return
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as handle:
        json.dump(
            [dict(entry, id=uuid.uuid4().hex[:8]) for entry in config.BIN_RULES],
            handle,
            indent=2,
        )


def _read() -> List[dict]:
    _ensure_file()
    sig = _signature()
    if _cache["rules"] is None or _cache["sig"] != sig:
        with open(_PATH, "r", encoding="utf-8") as handle:
            entries = json.load(handle)
        if not isinstance(entries, list):
            raise ValueError("bin_rules.json must contain a JSON list of rules.")
        _cache["rules"] = [dict(e) for e in entries]
        _cache["sig"] = sig
    return [dict(e) for e in _cache["rules"]]


def _save(entries: List[dict]) -> List[dict]:
    _ensure_file()
    with open(_PATH, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, indent=2)
    _cache["rules"] = [dict(e) for e in entries]
    _cache["sig"] = _signature()
    return [dict(e) for e in entries]


def _index_of(entries: List[dict], rule_id: str) -> int:
    for index, rule in enumerate(entries):
        if rule.get("id") == rule_id:
            return index
    raise ValueError("Bin rule not found.")


def _coerce_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.")
    if number < 0:
        raise ValueError(f"{label} cannot be negative.")
    return number


def _normalise(payload: Dict[str, Any], existing: Optional[dict]) -> dict:
    """Merge the payload over the existing rule (or empty) and validate it."""
    merged = dict(existing or {})
    for field in REQUIRED_FIELDS:
        if field in payload and payload[field] is not None and payload[field] != "":
            merged[field] = payload[field]

    if not str(merged.get("name", "")).strip():
        raise ValueError("Bin Type (name) is required.")
    merged["name"] = str(merged["name"]).strip()

    min_volume = _coerce_number(merged.get("min_volume"), "Minimum volume")
    max_volume = _coerce_number(merged.get("max_volume"), "Maximum volume")
    min_weight = _coerce_number(merged.get("min_weight"), "Minimum weight")
    max_weight = _coerce_number(merged.get("max_weight"), "Maximum weight")

    if max_volume <= 0 or max_weight <= 0:
        raise ValueError("Maximum volume and maximum weight must be greater than zero.")
    if max_volume < min_volume:
        raise ValueError("Maximum volume cannot be below minimum volume.")
    if max_weight < min_weight:
        raise ValueError("Maximum weight cannot be below minimum weight.")

    return {
        "id": existing.get("id") if existing else uuid.uuid4().hex[:8],
        "name": merged["name"],
        "min_volume": min_volume,
        "max_volume": max_volume,
        "min_weight": min_weight,
        "max_weight": max_weight,
    }


def _ensure_unique(entries: List[dict], name: str, exclude_id: Optional[str]) -> None:
    for rule in entries:
        if rule.get("name") == name and rule.get("id") != exclude_id:
            raise ValueError(f"A bin rule named '{name}' already exists.")


def _with_priority(entries: List[dict]) -> List[dict]:
    rules = [dict(e) for e in entries]
    for index, rule in enumerate(rules):
        rule["priority"] = index
    return rules


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_rules() -> List[dict]:
    """All rules in declaration order, each with ``id`` and 0-based ``priority``."""
    with _lock:
        return _with_priority(_read())


def get(rule_id: str) -> Optional[dict]:
    """A single rule by id, or None."""
    with _lock:
        for rule in _read():
            if rule.get("id") == rule_id:
                return dict(rule)
    return None


def create_rule(payload: Dict[str, Any]) -> dict:
    with _lock:
        entries = _read()
        rule = _normalise(payload, None)
        _ensure_unique(entries, rule["name"], None)
        entries.append(rule)
        _save(entries)
    return {**rule, "priority": len(entries) - 1}


def update_rule(rule_id: str, payload: Dict[str, Any]) -> dict:
    with _lock:
        entries = _read()
        index = _index_of(entries, rule_id)
        rule = _normalise(payload, entries[index])
        _ensure_unique(entries, rule["name"], rule_id)
        entries[index] = rule
        _save(entries)
    return {**rule, "priority": index}


def delete_rule(rule_id: str) -> None:
    with _lock:
        entries = _read()
        index = _index_of(entries, rule_id)
        if len(entries) <= 1:
            raise ValueError("At least one bin rule must remain.")
        del entries[index]
        _save(entries)


def reorder_rules(ids: List[str]) -> List[dict]:
    with _lock:
        entries = _read()
        by_id = {rule["id"]: rule for rule in entries}
        if len(ids) != len(entries) or any(rule_id not in by_id for rule_id in ids):
            raise ValueError("Reorder must list every bin rule exactly once.")
        ordered = [by_id[rule_id] for rule_id in ids]
        _save(ordered)
    return _with_priority(ordered)
