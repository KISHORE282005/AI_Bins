# Material Bin Recommendation System

Upload an Excel workbook and the system recommends a storage bin for every
material, ranks the candidates, quotes the volume and weight utilisation, and
explains each decision in plain language.

Two workbook layouts are supported, and the sheets present decide which runs:

| Workbook contains             | What happens                                                                                       |
| ----------------------------- | -------------------------------------------------------------------------------------------------- |
| an **Input** sheet            | Demand product volume and demand product weight are matched against the**bin rule master** below |
| **Part Requirements + Master** | Demand is derived from ROP and matched against the bin list in the workbook                        |

---

## Quick start

```bash
pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000), then either drag a workbook onto the upload area or
press **Load sample workbook** to run the bundled dummy data.

**Load sample workbook** runs the first `.xlsx` in `data/`, which is
`data/Input.xlsx`.

Batch / headless use:

```bash
python run_cli.py data/Input.xlsx
python run_cli.py data/Material_Bin_Suggestion_Dummy_Data.xlsx
python run_cli.py Input.xlsx -o results.xlsx -v
```

Tests:

```bash
python -m unittest discover -s tests
```

---

## Expected workbook

Sheet names and column headers are matched loosely: case, spacing, punctuation
and `(unit)` suffixes are ignored, common synonyms are accepted, and a title row
above the header is tolerated. `Length (mm)`, `LENGTH`, and `Part Length` all
resolve to the same field. The sheet is read row by row, so 100 rows and 5,000
rows cost the same per row and nothing is hard coded to the sample data.

### Input

| Column                            | Required | Notes                                        |
| --------------------------------- | -------- | -------------------------------------------- |
| S No / DWG No / SAP No            | no       | Carried through to the results and searchable |
| DESC                              | no       | Shown as Description                          |
| Qty/Mc, ROP                       | no       | Reference only; the demand columns are trusted as they stand |
| length / breadth / height         | no       | Reference only                                |
| Weight (Kg)                       | no       | Reference only - shown, never matched on      |
| Product Volume (mm^3)             | no       | Reference only - shown, never matched on      |
| **Demand product volume (mm^3)**  | **yes**  | Primary input for the recommendation          |
| **demand product weights (Kg)**   | **yes**  | Primary input for the recommendation          |

Header spelling is normalised before matching, so `demand product weights (Kg)`,
`Demand Product Weights` and `demand  product  weights` all resolve to the same
field. The uploaded file is never modified; results are written to a new
workbook offered through **Download result**.

### Part Requirements

| Column                   | Required      | Notes                                           |
| ------------------------ | ------------- | ----------------------------------------------- |
| Part Number              | yes           | Blank rows are skipped                          |
| Part Description         | no            |                                                 |
| Length / Breadth / Width | for fit check | Must be positive                                |
| Total Volume             | no            | Recalculated; a mismatch is reported            |
| ROP (Qty)                | yes           | Must be greater than zero                       |
| Required Volume          | no            | Recalculated                                    |
| Material Weight/Unit     | no            | Without it, the weight check cannot be enforced |
| Required Weight          | no            | Recalculated                                    |
| Bin Suggestion           | no            | Overwritten by the result                       |
| Suggestion Reason        | no            | Added if missing                                |

### Master

| Column                       | Required      | Notes                                                  |
| ---------------------------- | ------------- | ------------------------------------------------------ |
| Bin ID                       | yes           | Duplicates are ignored with a warning                  |
| Bin Description              | no            |                                                        |
| Bin Length / Breadth / Width | for fit check |                                                        |
| Cubic Capacity               | yes*          | *Falls back to L x B x W                               |
| Max Weight                   | no            | Absent means no weight limit                           |
| Location                     | no            | Quoted in the recommendation reason                    |
| Status                       | no            | Only`Available` (and synonyms) take part in matching |

### Logic Guide

Optional. Read and carried through to the output workbook unchanged; it is
documentation, not input to the calculation.

---

## Bin rule master (Input sheet mode)

The six shipped categories are seeded from `config.BIN_RULES` into
`data/bin_rules.json` on first use, and the **Bin Recommendation Rules** card in
the UI edits that store (add / edit / delete / reorder via each row's action
dropdown). The engine always matches against the store, so changes apply on the
next analysis - use **Re-run analysis** to apply them to the current workbook
without re-uploading. Reset by deleting `data/bin_rules.json`; it re-seeds from
the config defaults.

| Bin Type      | Maximum Volume (mm³) | Minimum Weight (Kg) | Maximum Weight (Kg) | Priority |
| ------------- | ---------------------: | --------------------: | --------------------: | :--------: |
| MS PLASTIC    |              3,696,000 |                     0 |                    10 |     1     |
| S PLASTIC     |             96,000,000 |                    10 |                    15 |     2     |
| M PLASTIC     |            264,000,000 |                    15 |                    25 |     3     |
| L PLASTIC     |            768,000,000 |                    25 |                    40 |     4     |
| MS BIN        |            113,400,000 |                    40 |                 1,500 |     5     |
| MS BIN (MESH) |            364,500,000 |                    40 |                 1,500 |     6     |

**Both conditions must hold.** A category is eligible only when

```
min_volume <= demand_volume <= max_volume
AND
min_weight <= demand_weight <= max_weight
```

One condition on its own is never enough: a demand inside MS BIN's volume range
but weighing 25 Kg is *not* an MS BIN, and a 5 Kg demand of 10,000,000 mm³ is
*not* MS PLASTIC.

**Boundaries.** Both bounds are inclusive, so a weight of exactly 10, 15, 25 or
40 Kg qualifies for two categories at once. Ranking resolves that
deterministically - never at random:

1. smallest suitable category (least maximum volume, which also leaves the least
   unused capacity for a fixed demand);
2. closest weight capacity without exceeding it (least maximum weight);
3. the `config.BIN_RULES` declaration order.

So 10 Kg goes to MS PLASTIC rather than S PLASTIC, and 15 Kg to S PLASTIC rather
than M PLASTIC. Set `PREFER_PRIORITY_OVER_SIZE = True` to take the first
eligible category in declaration order instead.

**Utilisation.**

```
Volume Utilization % = demand_volume / bin_max_volume x 100
Weight Utilization % = demand_weight / bin_max_weight x 100
```

**When nothing is eligible** the result is `No Suitable Bin` - there is no
fallback category - and the reason names the condition that blocked it:

> No Suitable Bin. Demand volume of 63,345,750 mm³ is within MS BIN capacity,
> but demand weight of 2,145 Kg exceeds the MS BIN maximum weight of 1,500 Kg.

> No Suitable Bin. Demand weight of 814 Kg matches MS BIN (MESH), but the demand
> volume of 649,350,000 mm³ exceeds the MS BIN (MESH) maximum volume of
> 364,500,000 mm³.

A row whose demand volume or demand weight is missing, non numeric or negative
is reported as `Invalid Data` with the offending column named, listed under
Validation, and excluded from matching. It never crashes the run and is never
given a bin.

### Using the engine directly

```python
from core.bin_rules import recommend_bin, process_material_data

d = recommend_bin(79_200_000, 22)
d.recommended_bin           # "M PLASTIC"
d.status                    # "Matched"
d.volume_utilisation_pct    # 30.0
d.reason                    # "Matched with M PLASTIC because ..."

# A pandas DataFrame or a list of dict rows; returns the same shape with
# Recommended Bin, Recommendation Status, Demand Volume, Demand Weight,
# Volume Utilization %, Weight Utilization % and Recommendation Reason added.
processed = process_material_data(df)
```

---

## Calculation and matching rules (Master bin mode)

```
Total Volume    = Length x Breadth x Width
Required Volume = Total Volume x ROP
Required Weight = Material Weight/Unit x ROP
```

Values already present in the workbook are **recalculated rather than trusted**;
if a stored value disagrees, the calculated one is used and the discrepancy is
raised as a warning.

A bin is suitable only when all three checks pass:

| Check      | Rule                                                              |
| ---------- | ----------------------------------------------------------------- |
| Volume     | `Required Volume <= Cubic Capacity x VOLUME_UTILISATION_FACTOR` |
| Weight     | `Required Weight <= Max Weight x WEIGHT_UTILISATION_FACTOR`     |
| Dimensions | The part physically fits inside the bin                           |

**Orientation.** With `ALLOW_ORIENTATION` on (default), the part may be rotated.
A box fits inside another box under axis-aligned rotation exactly when its
descending-sorted dimensions are each within the bin's, so all six rotations are
covered by one comparison of the sorted triples. The reason text says when a
recommendation depended on rotating the part.

**Ranking.** Among all suitable bins the *smallest practical* one wins, ordered
by cubic capacity, then physical footprint, then weight limit — never simply the
first match.

**When nothing fits**, the result is `No Suitable Bin` and the reason names the
limiting factor: insufficient cubic capacity, insufficient weight capacity,
insufficient dimensions, or — when each constraint is satisfiable individually
but not by one bin — the closest candidate and the check it fails.

Rows that cannot be trusted (missing ROP, negative dimensions) are marked
`Data Error`, excluded from matching, and listed under Validation.

---

## Architecture

```
app.py                  Flask routes, upload handling, download cache
run_cli.py              command line entry point
config.py               sheet/column aliases, BIN_RULES, matching rules, tuning knobs
core/
    models.py           BinRule, BinDecision, InputMaterial, PartRequirement, StorageBin
    excel_reader.py     sheet resolution, header mapping, cell coercion, mode detection
    bin_rules.py        the Input-sheet rule engine: recommend_bin, process_material_data
    validators.py       row level checks, warning vs error
    calculations.py     volume and weight derivation (Master mode)
    bin_matcher.py      one part vs one bin: volume, weight, dimensions (Master mode)
    recommender.py      ranking, reason text, summary metrics (Master mode)
    excel_writer.py     result workbook rendering for both modes
    pipeline.py         picks the mode and wires the stages together
static/, templates/     UI
tests/test_engine.py    unit tests
```

Each stage is independent: the rule engine knows nothing about Excel, Flask or
the browser - `recommend_bin(demand_volume, demand_weight)` takes two numbers
and returns a decision - and the reader knows nothing about bins.
`core.pipeline.analyse_workbook(stream)` is the only entry point the web layer
uses, and no recommendation logic is duplicated in `app.py` or `app.js`.

### Scale

Bins are sorted by capacity once per run and the search binary-searches the
first bin large enough, so no part is compared against bins that cannot hold it.
Bin geometry and weight limits are precomputed, keeping the inner loop to three
comparisons. Workbooks are streamed with openpyxl in read-only mode.

Measured on 5,000 materials x 1,000 bins: parse 0.5s, match 0.7s, write 6.0s.

---

## Configuration

All tuning lives in `config.py`:

| Setting                             | Default                     | Effect                              |
| ----------------------------------- | --------------------------- | ----------------------------------- |
| `BIN_RULES`                       | six categories              | The bin rule master - the only place thresholds are written |
| `PREFER_PRIORITY_OVER_SIZE`       | `False`                   | Rank eligible categories by declaration order instead of size |
| `RULE_BOUND_TOLERANCE`            | `1e-9`                    | Slack so a demand exactly on a bound is not lost to rounding |
| `ALLOW_ORIENTATION`               | `True`                    | Permit rotating the part to fit     |
| `VOLUME_UTILISATION_FACTOR`       | `1.0`                     | Usable fraction of cubic capacity   |
| `WEIGHT_UTILISATION_FACTOR`       | `1.0`                     | Usable fraction of the weight limit |
| `AVAILABLE_STATUS_VALUES`         | `available`, `free`, … | Statuses eligible for matching      |
| `TREAT_BLANK_STATUS_AS_AVAILABLE` | `True`                    | How to read an empty Status cell    |
| `MAX_ALTERNATIVES`                | `3`                       | Runner-up bins recorded per part    |
| `MAX_UPLOAD_MB`                   | `25`                      | Upload size limit                   |

To leave handling clearance, set `VOLUME_UTILISATION_FACTOR = 0.85`; every
recommendation and reason updates accordingly.

---

## Output workbook

### Input sheet mode

| Sheet               | Contents                                                                                     |
| ------------------- | -------------------------------------------------------------------------------------------- |
| Summary             | Total products, matched, no suitable bin, total demand volume and weight, products per bin type |
| Bin Recommendations | Every Input row plus Recommended Bin, Recommendation Status, Volume/Weight Utilization % and the reason - colour coded by status |
| Bin Rule Master     | The six categories and how many products landed in each                                       |
| Validation Issues   | Every warning and error (omitted when clean)                                                  |

### Master bin mode

| Sheet             | Contents                                                                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Summary           | Headline metrics for the run                                                                                                                     |
| Part Requirements | Original columns plus Bin Suggestion, Suggestion Reason, Status, Location, utilisation, orientation flag, alternatives — colour coded by status |
| Master            | The bin master plus how many parts landed in each bin                                                                                            |
| Validation Issues | Every warning and error (omitted when clean)                                                                                                     |
| Logic Guide       | Carried through from the upload                                                                                                                  |

---

## Notes

- Results are held in memory for one hour (`DOWNLOAD_CACHE_TTL_SECONDS`) and
  served from `/api/download/<token>`; nothing is written to disk.
- Each material is matched independently against the master. The system reports
  how many parts were routed to each bin, but does not pack multiple different
  parts into one bin or deduct consumed capacity as it goes.
- `python app.py` starts the Flask development server. For production use a WSGI
  server, e.g. `waitress-serve --port=5000 app:app`.
