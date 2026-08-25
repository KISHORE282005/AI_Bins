"""Domain objects shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# Row status values used by the UI and the exported workbook.
STATUS_ASSIGNED = "ASSIGNED"
STATUS_UNASSIGNED = "UNASSIGNED"
STATUS_ERROR = "ERROR"

NO_SUITABLE_BIN = "No Suitable Bin"

# Status values for the Input-sheet rule mode.  "Invalid Data" is reserved for
# rows the engine refused to evaluate (non numeric or negative demand); a row
# that was evaluated is always either Matched or No Suitable Bin.
STATUS_MATCHED = "Matched"
STATUS_NO_SUITABLE_BIN = NO_SUITABLE_BIN
STATUS_INVALID_DATA = "Invalid Data"

# Analysis modes.
MODE_INPUT = "input"      # flat Input sheet matched against the bin rule master
MODE_MASTER = "master"    # Part Requirements matched against a Master bin sheet


@dataclass
class ValidationIssue:
    """A single problem found while reading or checking the workbook."""

    sheet: str
    row: Optional[int]
    reference: str          # part number / bin id, or "" when unknown
    severity: str           # "error" | "warning"
    message: str

    def as_dict(self) -> dict:
        return {
            "sheet": self.sheet,
            "row": self.row,
            "reference": self.reference,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass
class PartRequirement:
    """One material row from the Part Requirements sheet."""

    row: int
    part_number: str
    description: str = ""

    length: Optional[float] = None
    breadth: Optional[float] = None
    width: Optional[float] = None
    rop: Optional[float] = None
    weight_per_unit: Optional[float] = None

    # Values as they appeared in the uploaded workbook (may be blank).
    source_unit_volume: Optional[float] = None
    source_required_volume: Optional[float] = None
    source_required_weight: Optional[float] = None

    # Values computed by the calculation engine.
    unit_volume: Optional[float] = None
    required_volume: Optional[float] = None
    required_weight: Optional[float] = None

    # Set when the row cannot take part in matching.
    error: Optional[str] = None

    @property
    def has_dimensions(self) -> bool:
        return None not in (self.length, self.breadth, self.width)

    @property
    def dimensions(self) -> Optional[Tuple[float, float, float]]:
        if not self.has_dimensions:
            return None
        return (float(self.length), float(self.breadth), float(self.width))

    @property
    def is_processable(self) -> bool:
        return self.error is None


@dataclass
class StorageBin:
    """One bin row from the Master sheet."""

    row: int
    bin_id: str
    description: str = ""

    length: Optional[float] = None
    breadth: Optional[float] = None
    width: Optional[float] = None
    cubic_capacity: Optional[float] = None
    max_weight: Optional[float] = None
    location: str = ""
    status: str = ""

    is_available: bool = True
    error: Optional[str] = None

    @property
    def has_dimensions(self) -> bool:
        return None not in (self.length, self.breadth, self.width)

    @property
    def dimensions(self) -> Optional[Tuple[float, float, float]]:
        if not self.has_dimensions:
            return None
        return (float(self.length), float(self.breadth), float(self.width))

    @property
    def usable_capacity(self) -> float:
        """Cubic capacity, falling back to L x B x W when not supplied."""
        if self.cubic_capacity is not None:
            return float(self.cubic_capacity)
        dims = self.dimensions
        if dims is None:
            return 0.0
        return dims[0] * dims[1] * dims[2]

    def size_key(self) -> Tuple[float, float, float]:
        """Ranking key: smallest practical bin first."""
        dims = self.dimensions
        footprint = (dims[0] * dims[1] * dims[2]) if dims else self.usable_capacity
        return (self.usable_capacity, footprint, float(self.max_weight or 0.0))

    def as_dict(self) -> dict:
        return {
            "bin_id": self.bin_id,
            "description": self.description,
            "length": self.length,
            "breadth": self.breadth,
            "width": self.width,
            "cubic_capacity": self.usable_capacity,
            "max_weight": self.max_weight,
            "location": self.location,
            "status": self.status,
            "is_available": self.is_available,
        }


@dataclass
class FitResult:
    """Outcome of testing one part against one bin."""

    bin: StorageBin
    volume_ok: bool
    weight_ok: bool
    dimension_ok: bool
    dimension_checked: bool
    orientation_used: bool
    volume_utilisation: Optional[float] = None   # 0-1 of usable capacity
    weight_utilisation: Optional[float] = None   # 0-1 of usable max weight

    @property
    def is_suitable(self) -> bool:
        return self.volume_ok and self.weight_ok and self.dimension_ok

    def failed_checks(self) -> List[str]:
        failures = []
        if not self.volume_ok:
            failures.append("volume")
        if not self.weight_ok:
            failures.append("weight")
        if not self.dimension_ok:
            failures.append("dimensions")
        return failures


@dataclass
class Recommendation:
    """Final answer for one material row."""

    part: PartRequirement
    status: str
    bin_suggestion: str
    reason: str
    recommended_bin: Optional[StorageBin] = None
    volume_utilisation: Optional[float] = None
    weight_utilisation: Optional[float] = None
    orientation_used: bool = False
    suitable_bin_count: int = 0
    alternatives: List[str] = field(default_factory=list)
    recommended_quantity: Optional[float] = None

    def as_dict(self) -> dict:
        p = self.part
        return {
            "row": p.row,
            "part_number": p.part_number,
            "description": p.description,
            "length": p.length,
            "breadth": p.breadth,
            "width": p.width,
            "unit_volume": p.unit_volume,
            "rop": p.rop,
            "required_volume": p.required_volume,
            "weight_per_unit": p.weight_per_unit,
            "required_weight": p.required_weight,
            "bin_suggestion": self.bin_suggestion,
            "reason": self.reason,
            "status": self.status,
            "location": self.recommended_bin.location if self.recommended_bin else "",
            "bin_capacity": self.recommended_bin.usable_capacity if self.recommended_bin else None,
            "bin_max_weight": self.recommended_bin.max_weight if self.recommended_bin else None,
            "volume_utilisation": self.volume_utilisation,
            "weight_utilisation": self.weight_utilisation,
            "orientation_used": self.orientation_used,
            "suitable_bin_count": self.suitable_bin_count,
            "alternatives": self.alternatives,
            "recommended_quantity": self.recommended_quantity,
        }


# ---------------------------------------------------------------------------
# Input-sheet rule mode
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BinRule:
    """One row of the bin rule master (see config.BIN_RULES).

    A demand is eligible only when it satisfies the volume band AND the weight
    band; `priority` is the declaration order, used to break ties.
    """

    name: str
    min_volume: float
    max_volume: float
    min_weight: float
    max_weight: float
    priority: int = 0

    def accepts_volume(self, volume: float, tolerance: float = 0.0) -> bool:
        return (self.min_volume - tolerance) <= volume <= (self.max_volume + tolerance)

    def accepts_weight(self, weight: float, tolerance: float = 0.0) -> bool:
        return (self.min_weight - tolerance) <= weight <= (self.max_weight + tolerance)

    def accepts(self, volume: float, weight: float, tolerance: float = 0.0) -> bool:
        """Both conditions must hold - one on its own never qualifies."""
        return self.accepts_volume(volume, tolerance) and self.accepts_weight(weight, tolerance)

    def volume_utilisation(self, volume: float) -> Optional[float]:
        """Fraction of the category's maximum volume the demand takes up."""
        if not self.max_volume:
            return None
        return volume / self.max_volume

    def weight_utilisation(self, weight: float) -> Optional[float]:
        if not self.max_weight:
            return None
        return weight / self.max_weight

    def size_key(self) -> Tuple[float, float, int]:
        """Smallest suitable category first, then tightest weight band."""
        return (self.max_volume, self.max_weight, self.priority)

    def weight_range_label(self) -> str:
        return f"{_trim(self.min_weight)}-{_trim(self.max_weight)} Kg"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "min_volume": self.min_volume,
            "max_volume": self.max_volume,
            "min_weight": self.min_weight,
            "max_weight": self.max_weight,
            "priority": self.priority,
        }


def _trim(value: float) -> str:
    """1500.0 -> '1500', 12.50 -> '12.5'."""
    text = f"{float(value):,.3f}".rstrip("0").rstrip(".")
    return text or "0"


@dataclass
class BinDecision:
    """What recommend_bin() answers for a single (volume, weight) pair."""

    recommended_bin: str
    status: str
    reason: str
    demand_volume: Optional[float] = None
    demand_weight: Optional[float] = None
    volume_utilisation_pct: Optional[float] = None
    weight_utilisation_pct: Optional[float] = None
    rule: Optional[BinRule] = None
    eligible_bins: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    recommended_quantity: Optional[float] = None

    @property
    def is_matched(self) -> bool:
        return self.status == STATUS_MATCHED

    def as_dict(self) -> dict:
        return {
            "recommended_bin": self.recommended_bin,
            "status": self.status,
            "reason": self.reason,
            "demand_volume": self.demand_volume,
            "demand_weight": self.demand_weight,
            "volume_utilisation_pct": self.volume_utilisation_pct,
            "weight_utilisation_pct": self.weight_utilisation_pct,
            "eligible_bins": list(self.eligible_bins),
            "errors": list(self.errors),
            "recommended_quantity": self.recommended_quantity,
        }


@dataclass
class InputMaterial:
    """One row of the Input sheet, exactly as read (nothing is recalculated)."""

    row: int
    s_no: str = ""
    dwg_no: str = ""
    sap_no: str = ""
    description: str = ""
    qty_per_mc: Optional[float] = None
    rop: Optional[float] = None
    length: Optional[float] = None
    breadth: Optional[float] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    product_volume: Optional[float] = None

    # The two primary inputs for the recommendation.
    demand_volume: Optional[float] = None
    demand_weight: Optional[float] = None

    # Raw cells kept so validation can quote what was actually in the sheet.
    raw_demand_volume: object = None
    raw_demand_weight: object = None

    error: Optional[str] = None

    @property
    def reference(self) -> str:
        return self.sap_no or self.dwg_no or self.s_no or f"row {self.row}"

    @property
    def is_processable(self) -> bool:
        return self.error is None


@dataclass
class MaterialRecommendation:
    """An Input row paired with the rule engine's verdict."""

    material: InputMaterial
    decision: BinDecision

    def as_dict(self) -> dict:
        m = self.material
        d = self.decision
        return {
            "row": m.row,
            "s_no": m.s_no,
            "dwg_no": m.dwg_no,
            "sap_no": m.sap_no,
            "description": m.description,
            "qty_per_mc": m.qty_per_mc,
            "rop": m.rop,
            "length": m.length,
            "breadth": m.breadth,
            "height": m.height,
            "weight": m.weight,
            "product_volume": m.product_volume,
            "demand_volume": d.demand_volume if d.demand_volume is not None else m.demand_volume,
            "demand_weight": d.demand_weight if d.demand_weight is not None else m.demand_weight,
            "recommended_bin": d.recommended_bin,
            "status": d.status,
            "volume_utilisation_pct": d.volume_utilisation_pct,
            "weight_utilisation_pct": d.weight_utilisation_pct,
            "reason": d.reason,
            "eligible_bins": list(d.eligible_bins),
            "recommended_quantity": d.recommended_quantity,
        }


@dataclass
class AnalysisResult:
    """Everything one workbook run produces."""

    recommendations: List[Recommendation]
    bins: List[StorageBin]
    issues: List[ValidationIssue]
    summary: dict
    guide_rows: List[Sequence] = field(default_factory=list)
    source_name: str = ""

    # Input-sheet mode.  Empty in the Master-bin mode and vice versa.
    mode: str = MODE_MASTER
    materials: List[MaterialRecommendation] = field(default_factory=list)
    rules: List[BinRule] = field(default_factory=list)
