"""Recommendation ranking and reason generation.

The engine pre-sorts the available bins from smallest to largest once per run.
For each material it binary-searches the first bin large enough by volume and
scans forward, so the first bin that also clears the weight and dimension
checks is by construction the smallest practical bin - no need to score every
bin against every part.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from typing import Dict, List, Optional, Tuple

import config
from core import bin_matcher
from core.models import (
    NO_SUITABLE_BIN,
    STATUS_ASSIGNED,
    STATUS_ERROR,
    STATUS_UNASSIGNED,
    FitResult,
    PartRequirement,
    Recommendation,
    StorageBin,
)


def _fmt_volume(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.0f} {config.VOLUME_UNIT}"


def _fmt_weight(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f} {config.WEIGHT_UNIT}"


def _fmt_dims(dims) -> str:
    if not dims:
        return "n/a"
    return " x ".join(f"{d:,.0f}" for d in dims) + f" {config.DIMENSION_UNIT}"


class BinRecommendationEngine:
    """Matches materials against a fixed master bin list."""

    def __init__(self, bins: List[StorageBin]):
        self.all_bins = bins
        self.available: List[StorageBin] = sorted(
            (b for b in bins if b.is_available), key=StorageBin.size_key
        )
        # Non-decreasing, so it can be binary-searched.
        self._capacities: List[float] = [bin_matcher.usable_capacity(b) for b in self.available]

        # Geometry and limits are pre-computed once per run rather than per
        # (part, bin) pair.  The tolerance is folded into the stored limits so
        # the hot loop is a plain ">" comparison with no function calls.
        # With orientation allowed, a box fits under rotation exactly when its
        # descending-sorted dimensions are each within the bin's, so both sides
        # are stored sorted.  With orientation disabled the axes are compared
        # as entered, so both sides are stored in their natural order.
        slack = 1.0 + config.COMPARISON_TOLERANCE
        self._orientation = config.ALLOW_ORIENTATION
        self._bin_dims_cmp: List[Optional[Tuple[float, float, float]]] = []
        self._weight_limits: List[Optional[float]] = []
        for storage_bin in self.available:
            dims = storage_bin.dimensions
            if dims is None:
                self._bin_dims_cmp.append(None)
            else:
                ordered = sorted(dims, reverse=True) if self._orientation else dims
                self._bin_dims_cmp.append(
                    (ordered[0] * slack, ordered[1] * slack, ordered[2] * slack)
                )
            limit = bin_matcher.usable_max_weight(storage_bin)
            self._weight_limits.append(None if limit is None else limit * slack)

        self._largest_capacity_bin = max(
            self.available, key=bin_matcher.usable_capacity, default=None
        )
        weighted = [b for b in self.available if b.max_weight is not None]
        self._has_unlimited_weight_bin = len(weighted) < len(self.available)
        self._strongest_bin = max(weighted, key=lambda b: b.max_weight, default=None)

    # -- public API --------------------------------------------------------

    def _calc_recommended_quantity(self, part: PartRequirement) -> Optional[float]:
        """Calculate recommended quantity when no suitable bin is found.

        Formula:
            rec_by_vol = max_bin_capacity / unit_volume * ROP
            rec_by_weight = max_bin_weight / weight_per_unit
            recommended_quantity = min(rec_by_vol, rec_by_weight)

        Returns None when the calculation cannot be performed.
        """
        if not self.available:
            return None

        max_capacity = max(bin_matcher.usable_capacity(b) for b in self.available)
        weighted = [b for b in self.available if b.max_weight is not None]
        max_wt = max((bin_matcher.usable_max_weight(b) for b in weighted), default=0.0)

        rec_by_vol: Optional[float] = None
        rec_by_weight: Optional[float] = None

        unit_vol = part.unit_volume
        rop = part.rop
        wt = part.weight_per_unit

        if unit_vol and unit_vol > 0 and rop is not None and rop > 0:
            rec_by_vol = (max_capacity / unit_vol) * rop

        if wt and wt > 0:
            rec_by_weight = max_wt / wt

        candidates = [v for v in (rec_by_vol, rec_by_weight) if v is not None and v > 0]
        if not candidates:
            return None

        return math.floor(min(candidates))

    def recommend(self, part: PartRequirement) -> Recommendation:
        if not part.is_processable:
            return Recommendation(
                part=part,
                status=STATUS_ERROR,
                bin_suggestion="Data Error",
                reason=f"Not analysed because the source row is incomplete: {part.error}.",
            )

        if not self.available:
            rec_qty = self._calc_recommended_quantity(part)
            return Recommendation(
                part=part,
                status=STATUS_UNASSIGNED,
                bin_suggestion=NO_SUITABLE_BIN,
                reason="No bin in the Master sheet has an 'Available' status, so nothing could be matched.",
                recommended_quantity=rec_qty,
            )

        best, suitable_count, alternatives = self._find_best(part)

        if best is None:
            rec_qty = self._calc_recommended_quantity(part)
            return Recommendation(
                part=part,
                status=STATUS_UNASSIGNED,
                bin_suggestion=NO_SUITABLE_BIN,
                reason=self._explain_failure(part),
                recommended_quantity=rec_qty,
            )

        fit = bin_matcher.evaluate(part, best)
        return Recommendation(
            part=part,
            status=STATUS_ASSIGNED,
            bin_suggestion=best.bin_id,
            reason=self._explain_success(part, fit, suitable_count),
            recommended_bin=best,
            volume_utilisation=fit.volume_utilisation,
            weight_utilisation=fit.weight_utilisation,
            orientation_used=fit.orientation_used,
            suitable_bin_count=suitable_count,
            alternatives=alternatives,
        )

    def run(self, parts: List[PartRequirement]) -> List[Recommendation]:
        return [self.recommend(part) for part in parts]

    # -- searching ---------------------------------------------------------

    def _find_best(self, part: PartRequirement) -> Tuple[Optional[StorageBin], int, List[str]]:
        """Return (smallest suitable bin, count of suitable bins, runner-up ids)."""
        required_volume = float(part.required_volume or 0.0)
        required_weight = float(part.required_weight or 0.0)
        part_dims = part.dimensions
        if part_dims is None:
            part_cmp = None
        else:
            part_cmp = sorted(part_dims, reverse=True) if self._orientation else part_dims

        # Bins before this index cannot hold the volume, whatever else they
        # offer, because self._capacities is sorted ascending.
        start = bisect_left(self._capacities, required_volume * (1.0 - config.COMPARISON_TOLERANCE))

        available = self.available
        bin_dims = self._bin_dims_cmp
        weight_limits = self._weight_limits

        best: Optional[StorageBin] = None
        count = 0
        alternatives: List[str] = []

        for index in range(start, len(available)):
            limit = weight_limits[index]
            if limit is not None and required_weight > limit:
                continue

            if part_cmp is not None:
                candidate = bin_dims[index]
                if candidate is not None and (
                    part_cmp[0] > candidate[0]
                    or part_cmp[1] > candidate[1]
                    or part_cmp[2] > candidate[2]
                ):
                    continue

            count += 1
            if best is None:
                best = available[index]
            elif len(alternatives) < config.MAX_ALTERNATIVES:
                alternatives.append(available[index].bin_id)

        return best, count, alternatives

    # -- explanations ------------------------------------------------------

    def _explain_success(self, part: PartRequirement, fit: FitResult, suitable_count: int) -> str:
        storage_bin = fit.bin
        capacity = bin_matcher.usable_capacity(storage_bin)
        max_weight = bin_matcher.usable_max_weight(storage_bin)

        parts_of_reason = [
            f"{storage_bin.bin_id} selected because the required volume, weight and "
            "dimensions are within the bin capacity."
        ]

        volume_note = (
            f"Volume {_fmt_volume(part.required_volume)} of {_fmt_volume(capacity)}"
        )
        if fit.volume_utilisation is not None:
            volume_note += f" ({fit.volume_utilisation * 100:.1f}% used)"
        parts_of_reason.append(volume_note + ".")

        if max_weight is None:
            parts_of_reason.append(
                f"Weight {_fmt_weight(part.required_weight)} against a bin with no stated limit."
            )
        else:
            weight_note = f"Weight {_fmt_weight(part.required_weight)} of {_fmt_weight(max_weight)}"
            if fit.weight_utilisation is not None:
                weight_note += f" ({fit.weight_utilisation * 100:.1f}% used)"
            parts_of_reason.append(weight_note + ".")

        if fit.dimension_checked:
            dimension_note = (
                f"Part {_fmt_dims(part.dimensions)} fits bin {_fmt_dims(storage_bin.dimensions)}"
            )
            dimension_note += " after rotating the part." if fit.orientation_used else "."
            parts_of_reason.append(dimension_note)
        else:
            parts_of_reason.append(
                "Physical fit could not be verified because dimensions are incomplete."
            )

        if suitable_count > 1:
            parts_of_reason.append(
                f"Smallest of {suitable_count} suitable bins."
            )
        else:
            parts_of_reason.append("It is the only suitable bin.")

        if storage_bin.location:
            parts_of_reason.append(f"Location: {storage_bin.location}.")

        return " ".join(parts_of_reason)

    def _explain_failure(self, part: PartRequirement) -> str:
        """Diagnose why nothing matched, naming the limiting factor."""
        required_volume = float(part.required_volume or 0.0)
        required_weight = float(part.required_weight or 0.0)
        part_dims = part.dimensions

        if part_dims is None:
            part_cmp = None
        else:
            part_cmp = sorted(part_dims, reverse=True) if self._orientation else part_dims

        volume_capable = 0
        weight_capable = 0
        dimension_capable = 0
        closest: Optional[Tuple[int, StorageBin, List[str]]] = None

        # Same cheap comparisons as the search path - no per-bin allocation.
        for index, storage_bin in enumerate(self.available):
            failures: List[str] = []

            if self._capacities[index] * (1.0 + config.COMPARISON_TOLERANCE) >= required_volume:
                volume_capable += 1
            else:
                failures.append("volume")

            limit = self._weight_limits[index]
            if limit is None or required_weight <= limit:
                weight_capable += 1
            else:
                failures.append("weight")

            candidate = self._bin_dims_cmp[index]
            if part_cmp is None or candidate is None or (
                part_cmp[0] <= candidate[0]
                and part_cmp[1] <= candidate[1]
                and part_cmp[2] <= candidate[2]
            ):
                dimension_capable += 1
            else:
                failures.append("dimensions")

            if closest is None or len(failures) < closest[0]:
                closest = (len(failures), storage_bin, failures)

        limits: List[str] = []

        if volume_capable == 0:
            largest = self._largest_capacity_bin
            largest_capacity = bin_matcher.usable_capacity(largest) if largest else 0.0
            limits.append(
                f"insufficient cubic capacity - {_fmt_volume(required_volume)} is more than the "
                f"largest available bin {largest.bin_id if largest else 'n/a'} "
                f"({_fmt_volume(largest_capacity)})"
            )

        if weight_capable == 0 and not self._has_unlimited_weight_bin:
            strongest = self._strongest_bin
            strongest_limit = bin_matcher.usable_max_weight(strongest) if strongest else 0.0
            limits.append(
                f"insufficient weight capacity - {_fmt_weight(required_weight)} is more than the "
                f"strongest available bin {strongest.bin_id if strongest else 'n/a'} "
                f"({_fmt_weight(strongest_limit)})"
            )

        if dimension_capable == 0 and part_dims is not None:
            limits.append(
                f"insufficient dimensions - no available bin can physically hold a part of "
                f"{_fmt_dims(part_dims)}"
                + (" even when rotated" if config.ALLOW_ORIENTATION else "")
            )

        if limits:
            return f"{NO_SUITABLE_BIN}: " + "; ".join(limits) + "."

        # Every constraint is satisfiable somewhere, just not by one single bin.
        detail = ""
        if closest is not None:
            _, near_bin, failures = closest
            detail = (
                f" The closest candidate {near_bin.bin_id} still fails on "
                f"{' and '.join(failures)}."
            )
        return (
            f"{NO_SUITABLE_BIN}: no single available bin satisfies volume "
            f"({_fmt_volume(required_volume)}), weight ({_fmt_weight(required_weight)}) and "
            f"dimensions ({_fmt_dims(part_dims)}) at the same time.{detail}"
        )


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

def build_summary(
    recommendations: List[Recommendation],
    bins: List[StorageBin],
    issues: List,
) -> Dict:
    assigned = [r for r in recommendations if r.status == STATUS_ASSIGNED]
    unassigned = [r for r in recommendations if r.status == STATUS_UNASSIGNED]
    errors = [r for r in recommendations if r.status == STATUS_ERROR]

    total_volume = sum(float(r.part.required_volume or 0.0) for r in recommendations)
    total_weight = sum(float(r.part.required_weight or 0.0) for r in recommendations)

    utilisations = [r.volume_utilisation for r in assigned if r.volume_utilisation is not None]
    bin_load: Dict[str, int] = {}
    for r in assigned:
        bin_load[r.bin_suggestion] = bin_load.get(r.bin_suggestion, 0) + 1

    available_bins = [b for b in bins if b.is_available]

    return {
        "mode": "master",
        "total_parts": len(recommendations),
        "assigned": len(assigned),
        "unassigned": len(unassigned) + len(errors),
        "no_suitable_bin": len(unassigned),
        "data_errors": len(errors),
        "total_required_volume": total_volume,
        "total_required_weight": total_weight,
        "total_bins": len(bins),
        "available_bins": len(available_bins),
        "distinct_bins_used": len(bin_load),
        "average_volume_utilisation": (sum(utilisations) / len(utilisations)) if utilisations else None,
        "orientation_used_count": sum(1 for r in assigned if r.orientation_used),
        "issue_count": len(issues),
        "error_issue_count": sum(1 for i in issues if i.severity == "error"),
        "bin_load": bin_load,
        "volume_unit": config.VOLUME_UNIT,
        "weight_unit": config.WEIGHT_UNIT,
        "dimension_unit": config.DIMENSION_UNIT,
    }
