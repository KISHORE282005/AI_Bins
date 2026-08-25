"""End-to-end orchestration: workbook in, AnalysisResult out.

This is the only module the web layer needs to know about.  Which of the two
analyses runs is decided by the sheets in the workbook:

    Input sheet                    -> the bin rule master in config.BIN_RULES
    Part Requirements + Master     -> the workbook's own bin list
"""

from __future__ import annotations

from typing import List

from core import bin_rules, calculations, excel_reader, recommender, validators
from core.excel_reader import WorkbookError, WorkbookContents  # re-exported for the web layer
from core.models import (
    MODE_INPUT,
    MODE_MASTER,
    AnalysisResult,
    MaterialRecommendation,
    ValidationIssue,
)

__all__ = ["analyse_workbook", "WorkbookError"]


def analyse_workbook(stream, source_name: str = "") -> AnalysisResult:
    """Read, validate and match a workbook stream."""
    contents = excel_reader.read_workbook(stream)
    if contents.mode == MODE_INPUT:
        return _analyse_input(contents, source_name)
    return _analyse_master(contents, source_name)


# ---------------------------------------------------------------------------
# Input sheet + bin rule master
# ---------------------------------------------------------------------------

def _analyse_input(contents: WorkbookContents, source_name: str) -> AnalysisResult:
    issues: List[ValidationIssue] = list(contents.issues)

    validators.validate_materials(contents.materials, issues)

    # The rule engine is asked about the raw cells so it does its own strict
    # numeric check; a row already flagged by the validator comes back as
    # "Invalid Data" from the same code path, never as a silent fallback bin.
    # The current rule master is fetched once per run so a mid-run edit to the
    # rule store cannot produce a mixed recommendation pass.
    rules = bin_rules.get_rules()
    results = [
        MaterialRecommendation(
            material=material,
            decision=bin_rules.recommend_bin(
                material.raw_demand_volume, material.raw_demand_weight, rules,
                rop=material.rop,
                product_volume=material.product_volume,
                weight_per_unit=material.weight,
            ),
        )
        for material in contents.materials
    ]

    return AnalysisResult(
        recommendations=[],
        bins=[],
        issues=issues,
        summary=bin_rules.build_summary(results, issues),
        guide_rows=contents.guide_rows,
        source_name=source_name,
        mode=MODE_INPUT,
        materials=results,
        rules=rules,
    )


# ---------------------------------------------------------------------------
# Part Requirements + Master bin sheet
# ---------------------------------------------------------------------------

def _analyse_master(contents: WorkbookContents, source_name: str) -> AnalysisResult:
    issues: List[ValidationIssue] = list(contents.issues)

    validators.validate_bins(contents.bins, issues)
    validators.validate_parts(contents.parts, issues)

    calculations.compute_all(contents.parts, issues)

    engine = recommender.BinRecommendationEngine(contents.bins)
    recommendations = engine.run(contents.parts)

    return AnalysisResult(
        recommendations=recommendations,
        bins=contents.bins,
        issues=issues,
        summary=recommender.build_summary(recommendations, contents.bins, issues),
        guide_rows=contents.guide_rows,
        source_name=source_name,
        mode=MODE_MASTER,
    )
