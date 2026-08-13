"""Row level validation.

Runs after parsing and before matching.  Rows with a fatal problem get an
`error` set so the recommender skips them; everything else is reported as a
warning and the row still flows through the pipeline.
"""

from __future__ import annotations

from typing import List

import config
from core import bin_rules
from core.models import InputMaterial, PartRequirement, StorageBin, ValidationIssue

PART_SHEET_LABEL = "Part Requirements"
BIN_SHEET_LABEL = "Master"
INPUT_SHEET_LABEL = "Input"


def validate_materials(materials: List[InputMaterial], issues: List[ValidationIssue]) -> None:
    """Check the two demand columns on every Input row.

    A row whose demand volume or demand weight is missing, non numeric or
    negative is marked with an error naming the offending column, and is
    reported as "Invalid Data" rather than being silently matched or crashing
    the run.  Everything else is a warning and the row still flows through.
    """
    for material in materials:
        volume, weight, problems = bin_rules.validate_demand(
            material.raw_demand_volume, material.raw_demand_weight
        )
        material.demand_volume = volume
        material.demand_weight = weight

        if problems:
            material.error = "; ".join(problems)
            issues.append(ValidationIssue(
                INPUT_SHEET_LABEL, material.row, material.reference, "error",
                f"Row excluded from matching - {material.error}.",
            ))
            continue

        if not material.sap_no and not material.dwg_no:
            issues.append(ValidationIssue(
                INPUT_SHEET_LABEL, material.row, material.reference, "warning",
                "Row has neither a SAP No nor a DWG No; it is still analysed.",
            ))

        if material.rop is not None and material.rop <= 0:
            issues.append(ValidationIssue(
                INPUT_SHEET_LABEL, material.row, material.reference, "warning",
                f"ROP is {material.rop:g}; the demand figures in the sheet are used as they stand.",
            ))


def validate_parts(parts: List[PartRequirement], issues: List[ValidationIssue]) -> None:
    for part in parts:
        problems: List[str] = []

        if part.rop is None:
            problems.append("ROP is missing")
        elif part.rop <= 0:
            problems.append(f"ROP must be greater than zero (found {part.rop:g})")

        dims = {"Length": part.length, "Breadth": part.breadth, "Width": part.width}
        missing_dims = [name for name, value in dims.items() if value is None]
        bad_dims = [name for name, value in dims.items() if value is not None and value <= 0]

        if bad_dims:
            problems.append(f"{', '.join(bad_dims)} must be greater than zero")

        if missing_dims:
            if part.source_unit_volume is None:
                problems.append(f"{', '.join(missing_dims)} missing and no Total Volume to fall back on")
            else:
                issues.append(ValidationIssue(
                    PART_SHEET_LABEL, part.row, part.part_number, "warning",
                    f"{', '.join(missing_dims)} missing; the physical fit check will be skipped "
                    "and only volume and weight are validated.",
                ))

        if part.weight_per_unit is None and part.source_required_weight is None:
            issues.append(ValidationIssue(
                PART_SHEET_LABEL, part.row, part.part_number, "warning",
                "No Material Weight/Unit; required weight is treated as zero and the "
                "weight check cannot be enforced.",
            ))
        elif part.weight_per_unit is not None and part.weight_per_unit < 0:
            problems.append(f"Material Weight/Unit cannot be negative (found {part.weight_per_unit:g})")

        if problems:
            part.error = "; ".join(problems)
            issues.append(ValidationIssue(
                PART_SHEET_LABEL, part.row, part.part_number, "error",
                f"Row excluded from matching - {part.error}.",
            ))


def validate_bins(bins: List[StorageBin], issues: List[ValidationIssue]) -> None:
    for storage_bin in bins:
        problems: List[str] = []

        if storage_bin.cubic_capacity is None and not storage_bin.has_dimensions:
            problems.append("no Cubic Capacity and no complete set of dimensions")
        elif storage_bin.cubic_capacity is not None and storage_bin.cubic_capacity <= 0:
            problems.append(f"Cubic Capacity must be greater than zero (found {storage_bin.cubic_capacity:g})")

        for name, value in (("Length", storage_bin.length),
                            ("Breadth", storage_bin.breadth),
                            ("Width", storage_bin.width)):
            if value is not None and value <= 0:
                problems.append(f"{name} must be greater than zero")

        if storage_bin.max_weight is None:
            issues.append(ValidationIssue(
                BIN_SHEET_LABEL, storage_bin.row, storage_bin.bin_id, "warning",
                "No Max Weight; this bin is treated as having no weight limit.",
            ))
        elif storage_bin.max_weight < 0:
            problems.append(f"Max Weight cannot be negative (found {storage_bin.max_weight:g})")

        if storage_bin.has_dimensions and storage_bin.cubic_capacity is not None:
            geometric = storage_bin.length * storage_bin.breadth * storage_bin.width
            if geometric > 0 and abs(storage_bin.cubic_capacity - geometric) / geometric > 0.01:
                issues.append(ValidationIssue(
                    BIN_SHEET_LABEL, storage_bin.row, storage_bin.bin_id, "warning",
                    f"Stated Cubic Capacity ({storage_bin.cubic_capacity:,.0f} {config.VOLUME_UNIT}) "
                    f"differs from L x B x W ({geometric:,.0f} {config.VOLUME_UNIT}); "
                    "the stated capacity is used.",
                ))

        if not storage_bin.has_dimensions:
            issues.append(ValidationIssue(
                BIN_SHEET_LABEL, storage_bin.row, storage_bin.bin_id, "warning",
                "Incomplete bin dimensions; physical fit cannot be verified against this bin.",
            ))

        if problems:
            storage_bin.error = "; ".join(problems)
            storage_bin.is_available = False
            issues.append(ValidationIssue(
                BIN_SHEET_LABEL, storage_bin.row, storage_bin.bin_id, "error",
                f"Bin excluded from matching - {storage_bin.error}.",
            ))

    if not any(b.is_available for b in bins):
        issues.append(ValidationIssue(
            BIN_SHEET_LABEL, None, "", "error",
            "No bin in the Master sheet is available for matching. Check the Status column.",
        ))
