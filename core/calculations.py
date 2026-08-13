"""Derived quantities for each material row.

    Total Volume    = Length x Breadth x Width
    Required Volume = Total Volume x ROP
    Required Weight = Material Weight/Unit x ROP

Values are always recalculated from the source dimensions rather than trusted
from the workbook, but a pre-filled value that disagrees with the recalculated
one is reported so the planner can see the discrepancy.
"""

from __future__ import annotations

from typing import List, Optional

import config
from core.models import PartRequirement, ValidationIssue

SHEET_LABEL = "Part Requirements"


def unit_volume(length: float, breadth: float, width: float) -> float:
    return float(length) * float(breadth) * float(width)


def required_volume(volume_per_unit: float, rop: float) -> float:
    return float(volume_per_unit) * float(rop)


def required_weight(weight_per_unit: float, rop: float) -> float:
    return float(weight_per_unit) * float(rop)


def _disagrees(computed: float, supplied: Optional[float]) -> bool:
    if supplied is None:
        return False
    if computed == 0:
        return abs(supplied) > config.RECALCULATION_TOLERANCE
    return abs(computed - supplied) / abs(computed) > config.RECALCULATION_TOLERANCE


def compute_part(part: PartRequirement, issues: List[ValidationIssue]) -> None:
    """Fill unit_volume / required_volume / required_weight on one part."""
    if not part.is_processable:
        return

    rop = float(part.rop)

    if part.has_dimensions:
        part.unit_volume = unit_volume(part.length, part.breadth, part.width)
        if _disagrees(part.unit_volume, part.source_unit_volume):
            issues.append(ValidationIssue(
                SHEET_LABEL, part.row, part.part_number, "warning",
                f"Total Volume in the file ({part.source_unit_volume:,.0f} {config.VOLUME_UNIT}) "
                f"differs from Length x Breadth x Width ({part.unit_volume:,.0f} "
                f"{config.VOLUME_UNIT}); the calculated value is used.",
            ))
    else:
        # No dimensions, but validation let the row through because the sheet
        # already carries a unit volume.
        part.unit_volume = float(part.source_unit_volume or 0.0)

    part.required_volume = required_volume(part.unit_volume, rop)
    if _disagrees(part.required_volume, part.source_required_volume):
        issues.append(ValidationIssue(
            SHEET_LABEL, part.row, part.part_number, "warning",
            f"Required Volume in the file ({part.source_required_volume:,.0f} "
            f"{config.VOLUME_UNIT}) differs from Total Volume x ROP "
            f"({part.required_volume:,.0f} {config.VOLUME_UNIT}); the calculated value is used.",
        ))

    if part.weight_per_unit is not None:
        part.required_weight = required_weight(part.weight_per_unit, rop)
        if _disagrees(part.required_weight, part.source_required_weight):
            issues.append(ValidationIssue(
                SHEET_LABEL, part.row, part.part_number, "warning",
                f"Required Weight in the file ({part.source_required_weight:,.3f} "
                f"{config.WEIGHT_UNIT}) differs from Material Weight/Unit x ROP "
                f"({part.required_weight:,.3f} {config.WEIGHT_UNIT}); the calculated value is used.",
            ))
    else:
        part.required_weight = float(part.source_required_weight or 0.0)


def compute_all(parts: List[PartRequirement], issues: List[ValidationIssue]) -> None:
    for part in parts:
        compute_part(part, issues)
