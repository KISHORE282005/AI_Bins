"""Central configuration for the Material Bin Recommendation System.

Everything that a plant engineer may want to tune (sheet names, column
spellings, matching rules) lives here so that no business rule is buried
inside the processing code.
"""

# --------------------------------------------------------------------------
# Upload limits
# --------------------------------------------------------------------------
MAX_UPLOAD_MB = 25
ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}

# --------------------------------------------------------------------------
# Sheet resolution
#
# Sheet names are matched on a normalised form (lowercase, no spaces or
# punctuation).  The first alias that matches a sheet in the workbook wins,
# and an exact match always beats a partial one.
# --------------------------------------------------------------------------
PART_SHEET_ALIASES = (
    "partrequirements",
    "partrequirement",
    "partrequirment",
    "materialrequirements",
    "requirements",
    "parts",
    "material",
)

BIN_SHEET_ALIASES = (
    "master",
    "binmaster",
    "masterbin",
    "masterbins",
    "bins",
    "storagebins",
)

GUIDE_SHEET_ALIASES = (
    "logicguide",
    "guide",
    "logic",
    "readme",
    "notes",
    "instructions",
)

# The "Input" sheet drives the rule-master mode: one flat sheet of materials
# that already carries the demand volume and demand weight, matched against the
# fixed BIN_RULES table below instead of against a Master bin sheet.
INPUT_SHEET_ALIASES = (
    "input",
    "inputsheet",
    "inputdata",
    "materialinput",
    "demandinput",
)

# --------------------------------------------------------------------------
# Column resolution
#
# Keys are the internal field names used across the application.  Values are
# the accepted header spellings in normalised form.  Headers are normalised by
# lowercasing, dropping any "(unit)" suffix and removing non-alphanumerics,
# so "Length (mm)" -> "length" and "Material Weight/Unit (kg)" ->
# "materialweightunit".
# --------------------------------------------------------------------------
PART_COLUMN_ALIASES = {
    "part_number": ("partnumber", "partno", "partcode", "partid", "materialcode", "itemcode"),
    "description": ("partdescription", "description", "partname", "materialdescription", "itemdescription"),
    "length": ("length", "partlength", "materiallength", "l"),
    "breadth": ("breadth", "partbreadth", "width2", "partwidth", "breadthb", "b"),
    "width": ("width", "height", "partheight", "thickness", "depth", "h", "w"),
    "unit_volume": ("totalvolume", "unitvolume", "volume", "volumeperunit", "partvolume"),
    "rop": ("rop", "ropqty", "reorderpoint", "reorderpointqty", "requiredqty", "requiredquantity", "quantity", "qty"),
    "required_volume": ("requiredvolume", "totalrequiredvolume"),
    "weight_per_unit": (
        "materialweightunit",
        "materialweightperunit",
        "weightperunit",
        "unitweight",
        "materialweight",
        "weightunit",
        "weight",
    ),
    "required_weight": ("requiredweight", "totalrequiredweight", "totalweight"),
    "bin_suggestion": ("binsuggestion", "suggestedbin", "recommendedbin", "bin"),
    "suggestion_reason": ("suggestionreason", "reason", "remarks", "recommendationreason"),
}

BIN_COLUMN_ALIASES = {
    "bin_id": ("binid", "bincode", "binno", "binnumber", "id"),
    "description": ("bindescription", "description", "binname", "bintype", "type"),
    "length": ("binlength", "length", "l"),
    "breadth": ("binbreadth", "breadth", "binwidth", "b"),
    "width": ("binwidth", "width", "binheight", "height", "bindepth", "depth", "h", "w"),
    "cubic_capacity": ("cubiccapacity", "capacity", "volumecapacity", "bincapacity", "binvolume", "volume"),
    "max_weight": ("maxweight", "maximumweight", "weightcapacity", "maxload", "loadcapacity", "capacitykg"),
    "location": ("location", "rack", "racklocation", "storagelocation", "position", "zone"),
    "status": ("status", "binstatus", "availability", "state"),
}

# Input sheet columns.  Only the two demand columns are required; everything
# else is carried through for display.  Headers are normalised the same way, so
# "demand product weights (Kg)", "Demand Product Weights" and
# "demand  product  weights" all resolve to demand_weight.
INPUT_COLUMN_ALIASES = {
    "s_no": ("sno", "serialno", "slno", "srno", "sinno", "sr"),
    "dwg_no": ("dwgno", "drawingno", "drawingnumber", "dwg", "drgno"),
    "sap_no": ("sapno", "sapnumber", "sapcode", "sap", "materialcode"),
    "description": ("desc", "description", "partdescription", "materialdescription", "itemdescription"),
    "qty_per_mc": ("qtymc", "qtypermc", "quantitypermc", "qtypermachine", "qtyperm"),
    "rop": ("rop", "reorderpoint", "reorderpointqty", "ropqty"),
    "length": ("length", "partlength", "l"),
    "breadth": ("breadth", "partbreadth", "width", "b"),
    "height": ("height", "partheight", "depth", "thickness", "h"),
    "weight": ("weight", "unitweight", "productweight", "weightperunit", "materialweightunit"),
    "product_volume": ("productvolume", "unitvolume", "volume", "volumeperunit"),
    "demand_volume": (
        "demandproductvolume",
        "demandproductvolumes",
        "demandvolume",
        "demandvolumes",
        "totaldemandvolume",
        "requiredvolume",
    ),
    "demand_weight": (
        "demandproductweights",
        "demandproductweight",
        "demandweights",
        "demandweight",
        "totaldemandweight",
        "requiredweight",
    ),
}

# Columns without which a row cannot be processed at all.
REQUIRED_PART_FIELDS = ("part_number", "rop")
REQUIRED_BIN_FIELDS = ("bin_id",)
REQUIRED_INPUT_FIELDS = ("demand_volume", "demand_weight")

# --------------------------------------------------------------------------
# Bin rule master
#
# The single source of truth for the Input-sheet mode.  A material is eligible
# for a category only when BOTH the demand volume AND the demand weight fall
# inside that category's range; one condition on its own is never enough.
#
# Bounds are inclusive on both ends (min <= value <= max).  Where two bands
# touch at a weight boundary (10, 15, 25, 40 kg) more than one category can be
# eligible, so declaration order below is the deterministic priority used to
# break ties - never a random or first-found choice.
#
# Add, remove or retune a category here; the engine reads this table and no
# threshold is repeated anywhere else in the code.
# --------------------------------------------------------------------------
BIN_RULES = [
    {"name": "MS PLASTIC",    "min_volume": 0, "max_volume": 3_696_000,   "min_weight": 0,  "max_weight": 10},
    {"name": "S PLASTIC",     "min_volume": 0, "max_volume": 96_000_000,  "min_weight": 10, "max_weight": 15},
    {"name": "M PLASTIC",     "min_volume": 0, "max_volume": 264_000_000, "min_weight": 15, "max_weight": 25},
    {"name": "L PLASTIC",     "min_volume": 0, "max_volume": 768_000_000, "min_weight": 25, "max_weight": 40},
    {"name": "MS BIN",        "min_volume": 0, "max_volume": 113_400_000, "min_weight": 40, "max_weight": 1500},
    {"name": "MS BIN (MESH)", "min_volume": 0, "max_volume": 364_500_000, "min_weight": 40, "max_weight": 1500},
]

# Ranking of the eligible categories.
#   False (default) - smallest suitable category first: least maximum volume,
#                     then the closest weight capacity, then the priority above.
#   True            - go straight down the BIN_RULES order and take the first
#                     eligible category.
PREFER_PRIORITY_OVER_SIZE = False

# Absolute slack applied to the rule bounds so a demand sitting exactly on a
# boundary (e.g. 10.000000000000002 kg after a floating point multiply) is not
# rejected by binary rounding.
RULE_BOUND_TOLERANCE = 1e-9

# --------------------------------------------------------------------------
# Matching rules
# --------------------------------------------------------------------------

# Only bins whose normalised status is in this set take part in matching.
AVAILABLE_STATUS_VALUES = {"available", "free", "empty", "open", "active", "ready", "unoccupied"}

# A blank Status cell is treated as available when True.
TREAT_BLANK_STATUS_AS_AVAILABLE = True

# Allow the part to be rotated so its dimensions can be re-arranged against
# the bin dimensions.  When False, Length->Length, Breadth->Breadth,
# Width->Width must hold in the given order.
ALLOW_ORIENTATION = True

# Fraction of a bin's cubic capacity that may actually be filled.  1.0 means
# the full nominal capacity is usable; lower it (e.g. 0.85) to leave handling
# clearance.
VOLUME_UTILISATION_FACTOR = 1.0

# Same idea for the weight limit.
WEIGHT_UTILISATION_FACTOR = 1.0

# Relative tolerance used when comparing floating point capacities so that a
# part needing exactly 100% of a bin is not rejected by binary rounding.
COMPARISON_TOLERANCE = 1e-9

# How many runner-up bins to keep for each part, shown as alternatives in the UI.
MAX_ALTERNATIVES = 3

# Tolerance (relative) before a value pre-filled in the workbook is reported as
# disagreeing with the recalculated value.
RECALCULATION_TOLERANCE = 1e-6

# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------
VOLUME_UNIT = "mm³"
WEIGHT_UNIT = "kg"
DIMENSION_UNIT = "mm"

# Cached analysis results available for download, and how long they live.
DOWNLOAD_CACHE_SIZE = 20
DOWNLOAD_CACHE_TTL_SECONDS = 60 * 60

# Folders searched (in order) for a bundled sample workbook. The first .xlsx
# found is what the "Load sample workbook" button uses.
SAMPLE_WORKBOOK_DIRS = ("data", "sample_data")
