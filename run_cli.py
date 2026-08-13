"""Command line runner - same pipeline as the web app, no browser needed.

    python run_cli.py input.xlsx
    python run_cli.py input.xlsx -o results.xlsx
"""

from __future__ import annotations

import argparse
import sys

from core import excel_writer
from core.models import MODE_INPUT, STATUS_ASSIGNED, STATUS_MATCHED
from core.pipeline import WorkbookError, analyse_workbook


def _report_input_mode(result, verbose: bool) -> None:
    """Print the bin rule master run."""
    summary = result.summary
    print(f"Products analysed     : {summary['total_products']}")
    print(f"Matched               : {summary['matched_products']}")
    print(f"No suitable bin       : {summary['no_suitable_bin']}")
    print(f"Invalid data rows     : {summary['invalid_products']}")
    print(f"Total demand volume   : {summary['total_demand_volume']:,.0f} mm3")
    print(f"Total demand weight   : {summary['total_demand_weight']:,.2f} Kg")
    print(f"Validation issues     : {summary['issue_count']}")

    print("\nProducts per bin type:")
    for name, count in summary["bin_load"].items():
        print(f"  {name:<16} {count}")

    rows = result.materials if verbose else [
        item for item in result.materials if item.decision.status != STATUS_MATCHED
    ]
    if rows:
        print("\nUnmatched materials:" if not verbose else "\nAll materials:")
        for item in rows:
            reference = item.material.sap_no or item.material.dwg_no or f"row {item.material.row}"
            print(f"  {reference:<12} -> {item.decision.recommended_bin:<16} {item.decision.reason}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Material bin recommendation")
    parser.add_argument(
        "workbook",
        help="An .xlsx with an Input sheet, or with Part Requirements and Master sheets",
    )
    parser.add_argument("-o", "--output", help="Output .xlsx (defaults to an auto-named file)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print every row")
    args = parser.parse_args(argv)

    try:
        with open(args.workbook, "rb") as handle:
            result = analyse_workbook(handle, source_name=args.workbook)
    except WorkbookError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: cannot open '{args.workbook}': {exc}", file=sys.stderr)
        return 2

    if result.mode == MODE_INPUT:
        _report_input_mode(result, args.verbose)
        output = args.output or excel_writer.output_filename(args.workbook)
        with open(output, "wb") as handle:
            handle.write(excel_writer.build_workbook(result))
        print(f"\nWritten: {output}")
        return 0

    summary = result.summary
    print(f"Parts analysed        : {summary['total_parts']}")
    print(f"Bins available        : {summary['available_bins']} of {summary['total_bins']}")
    print(f"Total required volume : {summary['total_required_volume']:,.0f} mm3")
    print(f"Total required weight : {summary['total_required_weight']:,.2f} kg")
    print(f"Assigned              : {summary['assigned']}")
    print(f"Unassigned            : {summary['unassigned']}")
    if summary["average_volume_utilisation"] is not None:
        print(f"Avg. utilisation      : {summary['average_volume_utilisation'] * 100:.1f}%")
    print(f"Validation issues     : {summary['issue_count']}")

    if args.verbose:
        print()
        for rec in result.recommendations:
            print(f"  {rec.part.part_number:<10} -> {rec.bin_suggestion:<16} {rec.reason}")
    else:
        unplaced = [r for r in result.recommendations if r.status != STATUS_ASSIGNED]
        if unplaced:
            print("\nUnassigned materials:")
            for rec in unplaced:
                print(f"  {rec.part.part_number:<10} {rec.reason}")

    output = args.output or excel_writer.output_filename(args.workbook)
    with open(output, "wb") as handle:
        handle.write(excel_writer.build_workbook(result))
    print(f"\nWritten: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
