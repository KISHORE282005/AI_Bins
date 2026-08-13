"""Unit tests for the calculation, matching and recommendation logic.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core import bin_matcher, bin_rules, calculations
from core.bin_rules import RuleDataError, process_material_data, recommend_bin
from core.excel_reader import map_columns, normalise, to_number
from core.models import (
    NO_SUITABLE_BIN,
    STATUS_ASSIGNED,
    STATUS_INVALID_DATA,
    STATUS_MATCHED,
    STATUS_NO_SUITABLE_BIN,
    STATUS_UNASSIGNED,
    PartRequirement,
    StorageBin,
)
from core.recommender import BinRecommendationEngine


def make_part(**kwargs) -> PartRequirement:
    defaults = dict(row=2, part_number="P-1", length=100.0, breadth=100.0,
                    width=100.0, rop=10.0, weight_per_unit=1.0)
    defaults.update(kwargs)
    part = PartRequirement(**defaults)
    calculations.compute_part(part, [])
    return part


def make_bin(bin_id, length, breadth, width, max_weight, capacity=None, available=True) -> StorageBin:
    return StorageBin(
        row=2, bin_id=bin_id, length=length, breadth=breadth, width=width,
        cubic_capacity=capacity if capacity is not None else length * breadth * width,
        max_weight=max_weight, status="Available" if available else "Occupied",
        is_available=available,
    )


class TestNormalisation(unittest.TestCase):
    def test_units_are_stripped_from_headers(self):
        self.assertEqual(normalise("Length (mm)"), "length")
        self.assertEqual(normalise("Total Volume (mm³)"), "totalvolume")
        self.assertEqual(normalise("Material Weight/Unit (kg)"), "materialweightunit")
        self.assertEqual(normalise("ROP (Qty)"), "rop")

    def test_numbers_tolerate_text(self):
        self.assertEqual(to_number("1,200"), 1200.0)
        self.assertEqual(to_number("300 mm"), 300.0)
        self.assertIsNone(to_number(""))
        self.assertIsNone(to_number(None))

    def test_breadth_and_width_do_not_collide(self):
        header = ["Bin ID", "Bin Length (mm)", "Bin Breadth (mm)", "Bin Width (mm)"]
        mapping = map_columns(header, config.BIN_COLUMN_ALIASES)
        self.assertEqual(mapping["length"], 1)
        self.assertEqual(mapping["breadth"], 2)
        self.assertEqual(mapping["width"], 3)


class TestCalculations(unittest.TestCase):
    def test_derived_values(self):
        part = make_part(length=300.0, breadth=200.0, width=100.0, rop=20.0, weight_per_unit=1.8)
        self.assertEqual(part.unit_volume, 6_000_000)
        self.assertEqual(part.required_volume, 120_000_000)
        self.assertAlmostEqual(part.required_weight, 36.0)

    def test_mismatch_with_workbook_value_is_reported(self):
        issues = []
        part = PartRequirement(row=2, part_number="P-1", length=10.0, breadth=10.0,
                               width=10.0, rop=1.0, weight_per_unit=1.0,
                               source_unit_volume=999.0)
        calculations.compute_part(part, issues)
        self.assertEqual(part.unit_volume, 1000.0)
        self.assertTrue(any("Total Volume" in i.message for i in issues))


class TestDimensionFit(unittest.TestCase):
    def test_direct_fit_needs_no_rotation(self):
        fits, rotated = bin_matcher.dimensions_fit((100, 80, 50), (200, 150, 100))
        self.assertTrue(fits)
        self.assertFalse(rotated)

    def test_rotation_rescues_a_fit(self):
        # 50 x 200 x 50 does not fit 100 x 300 x 100 in order, but does rotated.
        fits, rotated = bin_matcher.dimensions_fit((50, 200, 50), (100, 100, 300))
        self.assertTrue(fits)
        self.assertTrue(rotated)

    def test_genuinely_too_big_is_rejected(self):
        fits, _ = bin_matcher.dimensions_fit((400, 10, 10), (300, 300, 300))
        self.assertFalse(fits)

    def test_exact_fit_is_allowed(self):
        fits, _ = bin_matcher.dimensions_fit((300, 200, 100), (300, 200, 100))
        self.assertTrue(fits)


class TestRecommendation(unittest.TestCase):
    def setUp(self):
        self.bins = [
            make_bin("SMALL", 400, 300, 250, 100),
            make_bin("MEDIUM", 700, 500, 400, 300),
            make_bin("LARGE", 1000, 700, 600, 700),
        ]
        self.engine = BinRecommendationEngine(self.bins)

    def test_smallest_suitable_bin_wins(self):
        part = make_part(length=300, breadth=200, width=100, rop=1, weight_per_unit=1)
        result = self.engine.recommend(part)
        self.assertEqual(result.status, STATUS_ASSIGNED)
        self.assertEqual(result.bin_suggestion, "SMALL")
        self.assertEqual(result.suitable_bin_count, 3)

    def test_volume_pushes_to_a_larger_bin(self):
        # 6,000,000 x 20 = 120,000,000 -> too big for SMALL (30,000,000).
        part = make_part(length=300, breadth=200, width=100, rop=20, weight_per_unit=1.8)
        result = self.engine.recommend(part)
        self.assertEqual(result.bin_suggestion, "MEDIUM")

    def test_weight_pushes_to_a_larger_bin(self):
        part = make_part(length=100, breadth=100, width=100, rop=1, weight_per_unit=250)
        result = self.engine.recommend(part)
        self.assertEqual(result.bin_suggestion, "MEDIUM")

    def test_oversized_dimension_reports_dimension_limit(self):
        part = make_part(length=2000, breadth=10, width=10, rop=1, weight_per_unit=1)
        result = self.engine.recommend(part)
        self.assertEqual(result.status, STATUS_UNASSIGNED)
        self.assertEqual(result.bin_suggestion, "No Suitable Bin")
        self.assertIn("insufficient dimensions", result.reason)

    def test_oversized_volume_reports_capacity_limit(self):
        part = make_part(length=500, breadth=500, width=500, rop=1000, weight_per_unit=0.001)
        result = self.engine.recommend(part)
        self.assertEqual(result.status, STATUS_UNASSIGNED)
        self.assertIn("insufficient cubic capacity", result.reason)

    def test_overweight_reports_weight_limit(self):
        part = make_part(length=10, breadth=10, width=10, rop=1, weight_per_unit=5000)
        result = self.engine.recommend(part)
        self.assertEqual(result.status, STATUS_UNASSIGNED)
        self.assertIn("insufficient weight capacity", result.reason)

    def test_unavailable_bins_are_ignored(self):
        bins = [make_bin("SMALL", 400, 300, 250, 100, available=False),
                make_bin("MEDIUM", 700, 500, 400, 300)]
        engine = BinRecommendationEngine(bins)
        part = make_part(length=300, breadth=200, width=100, rop=1, weight_per_unit=1)
        self.assertEqual(engine.recommend(part).bin_suggestion, "MEDIUM")

    def test_reason_mentions_the_selected_bin(self):
        part = make_part(length=300, breadth=200, width=100, rop=1, weight_per_unit=1)
        result = self.engine.recommend(part)
        self.assertTrue(result.reason.startswith("SMALL selected because"))
        self.assertIn("within the bin capacity", result.reason)

    def test_invalid_row_is_flagged_not_matched(self):
        part = PartRequirement(row=9, part_number="P-BAD", rop=None)
        part.error = "ROP is missing"
        result = self.engine.recommend(part)
        self.assertEqual(result.bin_suggestion, "Data Error")


class TestBinRuleEngine(unittest.TestCase):
    """The Input-sheet rule master: config.BIN_RULES driven matching."""

    def test_both_conditions_must_hold(self):
        # 22 kg is squarely in the M PLASTIC weight band.
        result = recommend_bin(79_200_000, 22)
        self.assertEqual(result.status, STATUS_MATCHED)
        self.assertEqual(result.recommended_bin, "M PLASTIC")

    def test_volume_inside_ms_bin_but_weight_too_light(self):
        # MS BIN needs 40 kg; 30 kg must not be routed to it.
        result = recommend_bin(50_000_000, 30)
        self.assertEqual(result.recommended_bin, "L PLASTIC")
        self.assertNotIn("MS BIN", result.eligible_bins)

    def test_weight_inside_ms_plastic_but_volume_too_large(self):
        result = recommend_bin(4_000_000, 5)
        self.assertEqual(result.status, STATUS_NO_SUITABLE_BIN)
        self.assertEqual(result.recommended_bin, NO_SUITABLE_BIN)
        self.assertIn("exceeds the MS PLASTIC maximum volume", result.reason)

    def test_weight_below_ms_bin_minimum_is_explained(self):
        # Volume fits every plastic bin, weight sits in the gap above L PLASTIC.
        result = recommend_bin(1_000_000, 41)
        self.assertEqual(result.recommended_bin, "MS BIN")

    def test_overweight_reports_no_suitable_bin(self):
        result = recommend_bin(1_000_000, 2_145)
        self.assertEqual(result.status, STATUS_NO_SUITABLE_BIN)
        self.assertIn("exceeds the MS BIN maximum weight", result.reason)

    def test_oversized_volume_reports_no_suitable_bin(self):
        result = recommend_bin(922_670_000, 1_560.2)
        self.assertEqual(result.status, STATUS_NO_SUITABLE_BIN)
        self.assertIn("exceeds the largest category capacity", result.reason)

    def test_boundary_10kg_takes_the_lower_category(self):
        # Both MS PLASTIC (0-10) and S PLASTIC (10-15) accept 10 kg.
        result = recommend_bin(1_000_000, 10)
        self.assertEqual(result.recommended_bin, "MS PLASTIC")
        self.assertEqual(result.eligible_bins[:2], ["MS PLASTIC", "S PLASTIC"])

    def test_boundary_15kg_takes_the_lower_category(self):
        result = recommend_bin(1_000_000, 15)
        self.assertEqual(result.recommended_bin, "S PLASTIC")

    def test_boundary_25kg_takes_the_lower_category(self):
        result = recommend_bin(1_000_000, 25)
        self.assertEqual(result.recommended_bin, "M PLASTIC")

    def test_upper_and_lower_bounds_are_inclusive(self):
        self.assertEqual(recommend_bin(3_696_000, 10).recommended_bin, "MS PLASTIC")
        self.assertEqual(recommend_bin(0, 0).recommended_bin, "MS PLASTIC")
        self.assertEqual(recommend_bin(113_400_000, 1_500).recommended_bin, "MS BIN")

    def test_ranking_is_deterministic_not_first_found(self):
        # 45 kg / 200,000,000 mm3 is eligible for MS BIN (MESH) only; at a
        # smaller volume both MS BIN and MESH qualify and the smaller wins.
        self.assertEqual(recommend_bin(200_000_000, 45).recommended_bin, "MS BIN (MESH)")
        self.assertEqual(recommend_bin(100_000_000, 45).recommended_bin, "MS BIN")

    def test_utilisation_is_a_percentage_of_the_category_maximum(self):
        result = recommend_bin(132_000_000, 20)
        self.assertEqual(result.recommended_bin, "M PLASTIC")
        self.assertAlmostEqual(result.volume_utilisation_pct, 50.0)
        self.assertAlmostEqual(result.weight_utilisation_pct, 80.0)

    def test_reason_quotes_both_limits(self):
        result = recommend_bin(79_200_000, 22)
        self.assertIn("264,000,000", result.reason)
        self.assertIn("15–25 Kg", result.reason)

    def test_no_fallback_bin_is_ever_assigned(self):
        for volume, weight in [(1e12, 5), (1_000, 5_000), (1e12, 5_000)]:
            self.assertEqual(recommend_bin(volume, weight).recommended_bin, NO_SUITABLE_BIN)

    def test_invalid_data_does_not_crash(self):
        for volume, weight in [(None, 10), ("abc", 10), (10, None), (-5, 10), (10, -1), ("", "")]:
            result = recommend_bin(volume, weight)
            self.assertEqual(result.status, STATUS_INVALID_DATA)
            self.assertEqual(result.recommended_bin, NO_SUITABLE_BIN)
            self.assertTrue(result.errors)

    def test_numeric_text_is_accepted(self):
        result = recommend_bin("1,000,000", "22")
        self.assertEqual(result.status, STATUS_MATCHED)
        self.assertEqual(result.recommended_bin, "M PLASTIC")


class TestProcessMaterialData(unittest.TestCase):
    """Batch processing over dict rows, with tolerant header matching."""

    def rows(self, volume_header, weight_header):
        return [
            {"SAP No": 7038068, volume_header: 10_998_000, weight_header: 50},
            {"SAP No": 7038070, volume_header: 5_241_600, weight_header: 5},
        ]

    def test_headers_are_normalised(self):
        for volume_header, weight_header in [
            ("Demand product volume  (mm^3)", "demand product weights (Kg)"),
            ("DEMAND PRODUCT VOLUME", "Demand Product Weights"),
            ("demand product volume", "demand product weight"),
        ]:
            processed = process_material_data(self.rows(volume_header, weight_header))
            self.assertEqual(processed[0]["Recommended Bin"], "MS BIN")
            self.assertEqual(processed[0]["Recommendation Status"], STATUS_MATCHED)

    def test_output_columns_are_added(self):
        processed = process_material_data(self.rows("Demand product volume (mm^3)",
                                                    "demand product weights (Kg)"))
        for column in bin_rules.OUTPUT_COLUMNS:
            self.assertIn(column, processed[0])

    def test_source_rows_are_not_modified(self):
        source = self.rows("Demand product volume (mm^3)", "demand product weights (Kg)")
        process_material_data(source)
        self.assertNotIn("Recommended Bin", source[0])

    def test_second_row_is_matched_independently(self):
        processed = process_material_data(self.rows("Demand product volume (mm^3)",
                                                    "demand product weights (Kg)"))
        # 5,241,600 mm3 at 5 kg: weight suits MS PLASTIC but the volume does not.
        self.assertEqual(processed[1]["Recommended Bin"], NO_SUITABLE_BIN)
        self.assertEqual(processed[1]["Recommendation Status"], STATUS_NO_SUITABLE_BIN)

    def test_missing_required_column_is_reported(self):
        with self.assertRaises(RuleDataError) as caught:
            process_material_data([{"SAP No": 1, "Demand product volume (mm^3)": 10}])
        self.assertIn("demand product weights", str(caught.exception))

    def test_scales_to_many_rows(self):
        many = [{"Demand product volume (mm^3)": 1_000_000,
                 "demand product weights (Kg)": 20} for _ in range(5000)]
        processed = process_material_data(many)
        self.assertEqual(len(processed), 5000)
        self.assertEqual(processed[-1]["Recommended Bin"], "M PLASTIC")


if __name__ == "__main__":
    unittest.main()
