"""Feasibility checks between one material and one bin.

Three independent checks decide whether a bin can hold a material:

  volume      required volume  <= usable cubic capacity
  weight      required weight  <= usable maximum weight
  dimensions  the part physically fits inside the bin

The dimension check supports orientation.  A rectangular box fits inside
another box under axis-aligned rotation exactly when its sorted dimensions are
each <= the bin's sorted dimensions, so comparing the two descending-sorted
triples covers all six rotations in one pass.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

import config
from core.models import FitResult, PartRequirement, StorageBin


def _lte(value: float, limit: float) -> bool:
    """value <= limit, with a relative tolerance for float noise."""
    if value <= limit:
        return True
    scale = max(abs(value), abs(limit), 1.0)
    return (value - limit) <= config.COMPARISON_TOLERANCE * scale


def usable_capacity(storage_bin: StorageBin) -> float:
    return storage_bin.usable_capacity * config.VOLUME_UTILISATION_FACTOR


def usable_max_weight(storage_bin: StorageBin) -> Optional[float]:
    """None means the bin declares no weight limit."""
    if storage_bin.max_weight is None:
        return None
    return float(storage_bin.max_weight) * config.WEIGHT_UTILISATION_FACTOR


def dimensions_fit(
    part_dims: Sequence[float],
    bin_dims: Sequence[float],
    allow_orientation: bool = None,
) -> Tuple[bool, bool]:
    """Return (fits, orientation_used).

    orientation_used is True when the part only fits after being rotated, i.e.
    the as-entered Length/Breadth/Width order fails but some rotation works.
    """
    if allow_orientation is None:
        allow_orientation = config.ALLOW_ORIENTATION

    natural = all(_lte(p, b) for p, b in zip(part_dims, bin_dims))
    if natural:
        return True, False
    if not allow_orientation:
        return False, False

    rotated = all(
        _lte(p, b)
        for p, b in zip(sorted(part_dims, reverse=True), sorted(bin_dims, reverse=True))
    )
    return rotated, rotated


def evaluate(part: PartRequirement, storage_bin: StorageBin) -> FitResult:
    """Test one part against one bin and report every check individually."""
    capacity = usable_capacity(storage_bin)
    max_weight = usable_max_weight(storage_bin)

    required_volume = float(part.required_volume or 0.0)
    required_weight = float(part.required_weight or 0.0)

    volume_ok = capacity > 0 and _lte(required_volume, capacity)
    weight_ok = True if max_weight is None else _lte(required_weight, max_weight)

    part_dims = part.dimensions
    bin_dims = storage_bin.dimensions
    if part_dims is None or bin_dims is None:
        # Not enough geometry on one side; volume and weight still govern.
        dimension_ok, dimension_checked, orientation_used = True, False, False
    else:
        dimension_ok, orientation_used = dimensions_fit(part_dims, bin_dims)
        dimension_checked = True

    return FitResult(
        bin=storage_bin,
        volume_ok=volume_ok,
        weight_ok=weight_ok,
        dimension_ok=dimension_ok,
        dimension_checked=dimension_checked,
        orientation_used=orientation_used,
        volume_utilisation=(required_volume / capacity) if capacity > 0 else None,
        weight_utilisation=(
            (required_weight / max_weight) if max_weight not in (None, 0) else None
        ),
    )
