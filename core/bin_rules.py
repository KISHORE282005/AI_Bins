"""The bin rule engine.

This module owns every decision about which storage category a material
belongs in.  It knows nothing about Excel, Flask or the browser: it takes two
numbers - the demand volume and the demand weight - and answers with a
category, a utilisation figure and a sentence explaining itself.

    from core.bin_rules import recommend_bin, process_material_data

    decision = recommend_bin(79_200_000, 22)
    decision.recommended_bin     # "M PLASTIC"
    decision.status              # "Matched"

The rule table ships in ``config.BIN_RULES``, which seeds the persistent
``data/bin_rules.json`` store on first use.  ``get_rules()`` reads the store so
edits made through the UI's rule CRUD are picked up immediately; no threshold
is written down anywhere else.

Rules
    * Both conditions must hold.  A demand inside a category's volume band but
      outside its weight band is NOT eligible for it, and vice versa.
    * Bounds are inclusive at both ends.
    * Where bands touch (10, 15, 25, 40 kg) several categories can be eligible;
      the ranking below is fully deterministic, never first-found.
    * Nothing eligible means "No Suitable Bin" - there is no fallback category.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import config
from core.excel_reader import normalise
from core.models import (
    STATUS_INVALID_DATA,
    STATUS_MATCHED,
    STATUS_NO_SUITABLE_BIN,
    NO_SUITABLE_BIN,
    BinDecision,
    BinRule,
)

# The columns the engine appends to the processed data, in this order.
OUTPUT_COLUMNS = (
    "Recommended Bin",
    "Recommendation Status",
    "Demand Volume",
    "Demand Weight",
    "Volume Utilization %",
    "Weight Utilization %",
    "Recommendation Reason",
    "Recommended Quantity",
)

VOLUME_UNIT = "mm³"
WEIGHT_UNIT = "Kg"


class RuleDataError(ValueError):
    """Raised when a batch of rows cannot be processed at all."""


# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------

def load_rules(raw: Optional[Iterable[Mapping[str, Any]]] = None) -> List[BinRule]:
    """Turn the ``config.BIN_RULES`` dictionaries into BinRule objects.

    Declaration order becomes the priority used to break ties.
    """
    entries = config.BIN_RULES if raw is None else raw
    rules: List[BinRule] = []

    for index, entry in enumerate(entries):
        name = str(entry["name"]).strip()
        min_volume = float(entry.get("min_volume", 0) or 0)
        max_volume = float(entry["max_volume"])
        min_weight = float(entry.get("min_weight", 0) or 0)
        max_weight = float(entry["max_weight"])

        if max_volume < min_volume or max_weight < min_weight:
            raise RuleDataError(
                f"Bin rule '{name}' has a maximum below its minimum; check config.BIN_RULES."
            )

        rules.append(BinRule(
            name=name,
            min_volume=min_volume,
            max_volume=max_volume,
            min_weight=min_weight,
            max_weight=max_weight,
            priority=index,
        ))

    if not rules:
        raise RuleDataError("config.BIN_RULES is empty; there is nothing to match against.")
    return rules


# Loaded from config.BIN_RULES at import time.  It is the fallback table until
# the persistent rule store exists; callers that want the current (possibly
# edited) master should use get_rules() or pass rules= explicitly.
RULES: List[BinRule] = load_rules()


def get_rules() -> List[BinRule]:
    """The current rule master from the persistent rule store.

    The store is seeded from ``config.BIN_RULES`` on first use, so on a fresh
    install this is identical to ``RULES``; any edits made through the UI (or
    directly to the store) are reflected here immediately.
    """
    from core import rules_store

    return load_rules(rules_store.list_rules())


def rule_table() -> List[dict]:
    """The rule master as plain dictionaries, for the UI and the workbook."""
    return [rule.as_dict() for rule in get_rules()]


# ---------------------------------------------------------------------------
# Formatting helpers used inside the reason sentences
# ---------------------------------------------------------------------------

def _fmt_volume(value: float) -> str:
    return f"{float(value):,.0f} {VOLUME_UNIT}"


def _fmt_weight(value: float) -> str:
    text = f"{float(value):,.3f}".rstrip("0").rstrip(".")
    return f"{text or '0'} {WEIGHT_UNIT}"


def _fmt_pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _weight_band(rule: BinRule) -> str:
    low = f"{rule.min_weight:,.3f}".rstrip("0").rstrip(".") or "0"
    high = f"{rule.max_weight:,.3f}".rstrip("0").rstrip(".") or "0"
    return f"{low}–{high} {WEIGHT_UNIT}"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _to_number(value: Any) -> Tuple[Optional[float], Optional[str]]:
    """Coerce a cell to a float, or say why it cannot be one.

    Accepts int/float and numeric text with thousands separators ("1,200").
    Rejects blanks, booleans, NaN/inf and anything else - the demand columns
    are the primary inputs, so guessing at them is not safe.
    """
    if value is None:
        return None, "is missing"
    if isinstance(value, bool):
        return None, "is not numeric"
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None, "is not a finite number"
        return number, None

    text = str(value).strip()
    if not text:
        return None, "is missing"
    try:
        return float(text.replace(",", "").replace(" ", "")), None
    except ValueError:
        return None, f"is not numeric (found '{text}')"


def validate_demand(demand_volume: Any, demand_weight: Any) -> Tuple[Optional[float], Optional[float], List[str]]:
    """Return (volume, weight, problems).  Problems name the offending column."""
    problems: List[str] = []

    volume, volume_error = _to_number(demand_volume)
    if volume_error:
        problems.append(f"Demand product volume ({VOLUME_UNIT}) {volume_error}")
    elif volume < 0:
        problems.append(
            f"Demand product volume ({VOLUME_UNIT}) cannot be negative (found {_fmt_volume(volume)})"
        )
        volume = None

    weight, weight_error = _to_number(demand_weight)
    if weight_error:
        problems.append(f"Demand product weights ({WEIGHT_UNIT}) {weight_error}")
    elif weight < 0:
        problems.append(
            f"Demand product weights ({WEIGHT_UNIT}) cannot be negative (found {_fmt_weight(weight)})"
        )
        weight = None

    return volume, weight, problems


# ---------------------------------------------------------------------------
# The recommendation
# ---------------------------------------------------------------------------

def _rank(rules: List[BinRule]) -> List[BinRule]:
    """Order eligible categories best-first.

    Default ranking (config.PREFER_PRIORITY_OVER_SIZE = False):
      1. smallest suitable category - least maximum volume, which is also the
         one leaving the least unused capacity for a fixed demand;
      2. closest weight capacity without exceeding it - least maximum weight;
      3. the declared priority order, so ties never resolve at random.
    """
    if config.PREFER_PRIORITY_OVER_SIZE:
        return sorted(rules, key=lambda r: r.priority)
    return sorted(rules, key=BinRule.size_key)


def _explain_match(rule: BinRule, volume: float, weight: float, eligible: List[BinRule]) -> str:
    sentence = (
        f"Matched with {rule.name} because demand volume is {_fmt_volume(volume)} within the "
        f"maximum capacity of {_fmt_volume(rule.max_volume)} and demand weight is "
        f"{_fmt_weight(weight)} within the allowed range of {_weight_band(rule)}."
    )

    volume_pct = rule.volume_utilisation(volume)
    weight_pct = rule.weight_utilisation(weight)
    sentence += (
        f" Volume utilisation {_fmt_pct(None if volume_pct is None else volume_pct * 100)}, "
        f"weight utilisation {_fmt_pct(None if weight_pct is None else weight_pct * 100)}."
    )

    if len(eligible) > 1:
        others = ", ".join(r.name for r in eligible[1:])
        sentence += (
            f" Chosen as the smallest of {len(eligible)} eligible categories (also possible: {others})."
        )
    else:
        sentence += " It is the only eligible category."
    return sentence


def _explain_no_match(volume: float, weight: float, rules: List[BinRule]) -> str:
    """Say which of the two conditions blocked the match, and by how much."""
    weight_ok = [r for r in rules if r.accepts_weight(weight, config.RULE_BOUND_TOLERANCE)]
    volume_ok = [r for r in rules if r.accepts_volume(volume, config.RULE_BOUND_TOLERANCE)]

    if weight_ok:
        # The weight lands in a band, so volume is what failed everywhere.
        # Name the roomiest of those categories - the closest to fitting.
        near = max(weight_ok, key=lambda r: r.max_volume)
        others = [r.name for r in weight_ok if r is not near]
        if volume > near.max_volume:
            detail = (
                f"the demand volume of {_fmt_volume(volume)} exceeds the {near.name} maximum "
                f"volume of {_fmt_volume(near.max_volume)}"
            )
        else:
            detail = (
                f"the demand volume of {_fmt_volume(volume)} is below the {near.name} minimum "
                f"volume of {_fmt_volume(near.min_volume)}"
            )
        sentence = (
            f"{NO_SUITABLE_BIN}. Demand weight of {_fmt_weight(weight)} matches {near.name}, but {detail}."
        )
        if others:
            sentence += f" The other weight-compatible categories ({', '.join(others)}) are smaller still."
        return sentence

    if volume_ok:
        # Volume fits somewhere, so weight is outside every band of those.
        near = min(volume_ok, key=lambda r: min(abs(weight - r.min_weight), abs(weight - r.max_weight)))
        if weight < near.min_weight:
            detail = (
                f"demand weight of {_fmt_weight(weight)} is below the {near.name} minimum weight "
                f"requirement of {_fmt_weight(near.min_weight)}"
            )
        else:
            detail = (
                f"demand weight of {_fmt_weight(weight)} exceeds the {near.name} maximum weight "
                f"of {_fmt_weight(near.max_weight)}"
            )
        return (
            f"{NO_SUITABLE_BIN}. Demand volume of {_fmt_volume(volume)} is within {near.name} "
            f"capacity, but {detail}."
        )

    largest = max(rules, key=lambda r: r.max_volume)
    strongest = max(rules, key=lambda r: r.max_weight)
    return (
        f"{NO_SUITABLE_BIN}. Demand volume of {_fmt_volume(volume)} exceeds the largest category "
        f"capacity ({largest.name}, {_fmt_volume(largest.max_volume)}) and demand weight of "
        f"{_fmt_weight(weight)} is outside every weight band (heaviest is {strongest.name} at "
        f"{_fmt_weight(strongest.max_weight)})."
    )


def _calc_recommended_quantity(
    rules: List[BinRule],
    demand_volume: Optional[float],
    demand_weight: Optional[float],
    rop: Optional[float],
    product_volume: Optional[float],
    weight_per_unit: Optional[float],
) -> Optional[float]:
    """Calculate recommended quantity when no suitable bin is found.

    Formula:
        rec_by_vol = max_volume_of_all_rules / demand_product_volume * ROP
        rec_by_weight = max_weight_of_all_rules / individual_weight
        recommended_quantity = min(rec_by_vol, rec_by_weight)

    Returns None when the calculation cannot be performed (missing data or
    zero denominators).
    """
    if not rules:
        return None

    max_vol = max(r.max_volume for r in rules)
    max_wt = max(r.max_weight for r in rules)

    rec_by_vol: Optional[float] = None
    rec_by_weight: Optional[float] = None

    if demand_volume and demand_volume > 0 and rop is not None and rop > 0:
        rec_by_vol = (max_vol / demand_volume) * rop

    if weight_per_unit and weight_per_unit > 0:
        rec_by_weight = max_wt / weight_per_unit

    candidates = [v for v in (rec_by_vol, rec_by_weight) if v is not None and v > 0]
    if not candidates:
        return None

    return min(candidates)


def recommend_bin(
    demand_volume: Any,
    demand_weight: Any,
    rules: Optional[List[BinRule]] = None,
    rop: Optional[float] = None,
    product_volume: Optional[float] = None,
    weight_per_unit: Optional[float] = None,
) -> BinDecision:
    """Recommend a storage category for one material.

    The demand volume and demand weight are the only inputs; the product
    volume and the per-piece weight play no part in the decision.

    Steps: validate -> evaluate every rule -> collect the eligible ones ->
    rank them -> pick the best -> compute utilisation -> write the reason.
    Returns a BinDecision whose status is "Matched", "No Suitable Bin" or
    "Invalid Data".  There is no fallback category.

    When no bin matches, ``recommended_quantity`` is computed from the formula:
        min(max_volume_of_all_rules / demand_product_volume * ROP,
            max_weight_of_all_rules / individual_weight)
    """
    rules = get_rules() if rules is None else rules
    volume, weight, problems = validate_demand(demand_volume, demand_weight)

    if problems:
        return BinDecision(
            recommended_bin=NO_SUITABLE_BIN,
            status=STATUS_INVALID_DATA,
            reason="Not evaluated - " + "; ".join(problems) + ".",
            demand_volume=volume,
            demand_weight=weight,
            errors=problems,
        )

    tolerance = config.RULE_BOUND_TOLERANCE
    eligible = _rank([r for r in rules if r.accepts(volume, weight, tolerance)])

    if not eligible:
        rec_qty = _calc_recommended_quantity(
            rules, volume, weight, rop, product_volume, weight_per_unit
        )
        return BinDecision(
            recommended_bin=NO_SUITABLE_BIN,
            status=STATUS_NO_SUITABLE_BIN,
            reason=_explain_no_match(volume, weight, rules),
            demand_volume=volume,
            demand_weight=weight,
            recommended_quantity=rec_qty,
        )

    best = eligible[0]
    volume_pct = best.volume_utilisation(volume)
    weight_pct = best.weight_utilisation(weight)

    return BinDecision(
        recommended_bin=best.name,
        status=STATUS_MATCHED,
        reason=_explain_match(best, volume, weight, eligible),
        demand_volume=volume,
        demand_weight=weight,
        volume_utilisation_pct=None if volume_pct is None else volume_pct * 100.0,
        weight_utilisation_pct=None if weight_pct is None else weight_pct * 100.0,
        rule=best,
        eligible_bins=[r.name for r in eligible],
        recommended_quantity=float(rop) if rop is not None else None,
    )


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def resolve_columns(headers: Iterable[Any]) -> Dict[str, Any]:
    """Map internal field names to the actual header spellings.

    Headers are normalised first (case, spacing, punctuation and any "(unit)"
    suffix are dropped), so "demand product weights (Kg)",
    "Demand Product Weights" and "  DEMAND  PRODUCT  WEIGHTS " all resolve to
    the same field.
    """
    seen: List[Tuple[str, Any]] = [(normalise(h), h) for h in headers]
    claimed: set = set()
    mapping: Dict[str, Any] = {}

    for field, aliases in config.INPUT_COLUMN_ALIASES.items():
        for alias in aliases:
            for key, original in seen:
                if key == alias and key not in claimed:
                    mapping[field] = original
                    claimed.add(key)
                    break
            if field in mapping:
                break
    return mapping


def _is_frame(data: Any) -> bool:
    """True for a pandas DataFrame, without importing pandas."""
    return hasattr(data, "columns") and hasattr(data, "to_dict") and hasattr(data, "assign")


def process_material_data(df: Any, rules: Optional[List[BinRule]] = None) -> Any:
    """Apply recommend_bin() to every row of the input data.

    Accepts a pandas DataFrame or any sequence of mappings (dict rows), and
    returns the same shape with the seven output columns appended.  The
    original object is never modified in place.

    Raises RuleDataError when the two required demand columns are absent.
    """
    frame = _is_frame(df)

    if frame:
        headers = list(df.columns)
        records = df.to_dict("records")
    else:
        records = [dict(row) for row in df]
        headers = list(records[0].keys()) if records else []

    mapping = resolve_columns(headers)
    missing = [f for f in config.REQUIRED_INPUT_FIELDS if f not in mapping]
    if missing:
        pretty = {"demand_volume": "Demand product volume (mm^3)",
                  "demand_weight": "demand product weights (Kg)"}
        raise RuleDataError(
            "Required column(s) not found: "
            + ", ".join(pretty.get(f, f) for f in missing)
            + f". Columns present: {', '.join(str(h) for h in headers) or 'none'}."
        )

    volume_column = mapping["demand_volume"]
    weight_column = mapping["demand_weight"]

    rop_column = mapping.get("rop")
    product_volume_column = mapping.get("product_volume")
    weight_per_unit_column = mapping.get("weight")

    processed: List[dict] = []
    for record in records:
        decision = recommend_bin(
            record.get(volume_column),
            record.get(weight_column),
            rules,
            rop=record.get(rop_column) if rop_column else None,
            product_volume=record.get(product_volume_column) if product_volume_column else None,
            weight_per_unit=record.get(weight_per_unit_column) if weight_per_unit_column else None,
        )
        row = dict(record)
        row.update({
            "Recommended Bin": decision.recommended_bin,
            "Recommendation Status": decision.status,
            "Demand Volume": decision.demand_volume,
            "Demand Weight": decision.demand_weight,
            "Volume Utilization %": (
                None if decision.volume_utilisation_pct is None
                else round(decision.volume_utilisation_pct, 2)
            ),
            "Weight Utilization %": (
                None if decision.weight_utilisation_pct is None
                else round(decision.weight_utilisation_pct, 2)
            ),
            "Recommendation Reason": decision.reason,
            "Recommended Quantity": decision.recommended_quantity,
        })
        processed.append(row)

    if frame:
        import pandas as pd  # only needed when the caller already uses pandas

        return pd.DataFrame(processed, columns=list(headers) + [
            c for c in OUTPUT_COLUMNS if c not in headers
        ])
    return processed


# ---------------------------------------------------------------------------
# Dashboard metrics
# ---------------------------------------------------------------------------

def build_summary(results: Sequence, issues: Sequence = ()) -> dict:
    """Headline numbers for the summary dashboard.

    `results` is a sequence of MaterialRecommendation objects.
    """
    matched = [r for r in results if r.decision.status == STATUS_MATCHED]
    unmatched = [r for r in results if r.decision.status == STATUS_NO_SUITABLE_BIN]
    invalid = [r for r in results if r.decision.status == STATUS_INVALID_DATA]

    total_volume = sum(float(r.decision.demand_volume or 0.0) for r in results)
    total_weight = sum(float(r.decision.demand_weight or 0.0) for r in results)

    current = get_rules()
    bin_load: Dict[str, int] = {rule.name: 0 for rule in current}
    for r in matched:
        bin_load[r.decision.recommended_bin] = bin_load.get(r.decision.recommended_bin, 0) + 1

    volume_utils = [r.decision.volume_utilisation_pct for r in matched
                    if r.decision.volume_utilisation_pct is not None]
    weight_utils = [r.decision.weight_utilisation_pct for r in matched
                    if r.decision.weight_utilisation_pct is not None]

    return {
        "mode": "input",
        "total_products": len(results),
        "matched_products": len(matched),
        "no_suitable_bin": len(unmatched),
        "invalid_products": len(invalid),
        "total_demand_volume": total_volume,
        "total_demand_weight": total_weight,
        "average_volume_utilisation_pct": (sum(volume_utils) / len(volume_utils)) if volume_utils else None,
        "average_weight_utilisation_pct": (sum(weight_utils) / len(weight_utils)) if weight_utils else None,
        "bin_load": bin_load,
        "rule_count": len(current),
        "issue_count": len(issues),
        "error_issue_count": sum(1 for i in issues if getattr(i, "severity", "") == "error"),
        "volume_unit": VOLUME_UNIT,
        "weight_unit": WEIGHT_UNIT,
    }
