"""Core processing package for the Material Bin Recommendation System.

Stage separation:

    excel_reader  -> parse the workbook into domain objects, pick the mode
    validators    -> flag rows that cannot be trusted
    bin_rules     -> Input mode: match a demand against the bin rule master
    calculations  -> Master mode: derive total / required volume and weight
    bin_matcher   -> Master mode: feasibility for one part / bin pair
    recommender   -> Master mode: rank suitable bins and explain the choice
    excel_writer  -> render the result back to a downloadable workbook
    pipeline      -> wires the stages together
"""

__all__ = [
    "bin_matcher",
    "bin_rules",
    "calculations",
    "excel_reader",
    "excel_writer",
    "models",
    "pipeline",
    "recommender",
    "validators",
]
