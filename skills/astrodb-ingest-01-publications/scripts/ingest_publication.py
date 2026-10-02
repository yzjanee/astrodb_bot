"""
Template: ingest publications into an AstroDB Publications table.

This is a PATTERN to adapt — not a script to run as-is. Replace SETTINGS_FILE and the
PUBLICATIONS list with the user's real values (Steps 1-2 of the skill). For a batch,
build PUBLICATIONS from a table column instead of hand-listing it (see the commented
example near the bottom).
"""

import json
import logging
from pathlib import Path

from astrodb_utils import build_db_from_json
from astrodb_utils.publications import (
    ingest_publication,
    find_publication,
    check_ads_token,
)

logging.getLogger("astrodb_utils").setLevel(logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.basicConfig(format="%(levelname)s - %(message)s")

SAVE_DB = False  # set True only after a clean dry run, on explicit user confirmation

# Adjust to match your project layout (Step: Prerequisites)
SETTINGS_FILE = "database.toml"

# ADS auto-populates bibcode/doi/description from a DOI or bibcode, but needs a token.
# If no token is configured, set IGNORE_ADS = True and give each entry a `reference`.
IGNORE_ADS = not check_ads_token()
if IGNORE_ADS:
    logger.warning(
        "No ADS token found — ingesting with ignore_ads=True. "
        "Each publication must include a 'reference' shortname; "
        "bibcode/description will NOT be auto-fetched."
    )

# --- Publications to ingest — filled from Step 1-2 confirmation ---
# Each entry needs at least one identifier. With an ADS token, a DOI or bibcode is enough.
# Without a token (IGNORE_ADS=True), include a 'reference' shortname (and optionally a
# 'description'). Use ACTUAL values — never leave placeholder strings in the file.
PUBLICATIONS = [
    {"doi": "10.1088/0004-637X/748/2/93"},                 # auto-populated via ADS
    # {"bibcode": "2012ApJ...748...93R"},                  # bibcode also works
    # {"reference": "Rojas12",                             # no token / bare shortname:
    #  "description": "Discovery paper for ...",           #   supply metadata by hand
    #  "doi": "10.1088/0004-637X/748/2/93"},
    # {"data_value": "Rojas2012", "doi": "..."},           # batch: value as written in the
    #                                                      #   data table, for reference_map.json
]

db = build_db_from_json(settings_file=SETTINGS_FILE)

# Shared lookup from data-table reference values to Publications shortnames, read by the
# other ingest skills. Batch mode only: give each PUBLICATIONS entry a "data_value".
REFERENCE_MAP_PATH = Path("astrodb-ingest-artifacts/reference_map.json")
new_map_entries = {}  # data value -> shortname, for this run


def stored_reference(db, reference=None, doi=None, bibcode=None):
    """The shortname actually stored for this paper (ADS may have generated it)."""
    if reference:
        return reference
    for col, value in (("doi", doi), ("bibcode", bibcode)):
        if value:
            rows = db.query(db.Publications).filter(db.Publications.c[col] == value).all()
            if len(rows) == 1:
                return rows[0].reference
    return None


def read_reference_map(entries):
    """Load reference_map.json; stop (before anything is saved) if a value already maps elsewhere."""
    existing = json.loads(REFERENCE_MAP_PATH.read_text()) if REFERENCE_MAP_PATH.exists() else {}
    conflicts = {k: (existing[k], v) for k, v in entries.items() if k in existing and existing[k] != v}
    if conflicts:
        raise ValueError(f"reference_map.json already maps these differently — ask the user: {conflicts}")
    return existing


def update_reference_map(existing, entries):
    """Merge this run's entries into reference_map.json."""
    existing.update(entries)
    REFERENCE_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE_MAP_PATH.write_text(json.dumps(dict(sorted(existing.items())), indent=2) + "\n")
    logger.info(f"Updated {REFERENCE_MAP_PATH} ({len(entries)} values from this run)")


added = already_present = failed = 0
for pub in PUBLICATIONS:
    doi = pub.get("doi")
    bibcode = pub.get("bibcode")
    reference = pub.get("reference")
    label = reference or doi or bibcode

    # Deduplicate first — report existing rows instead of re-ingesting them.
    found, result = find_publication(db, reference=reference, doi=doi, bibcode=bibcode)
    if found:
        already_present += 1
        logger.info(f"Already present, skipping: {label} ({result})")
        if pub.get("data_value"):
            new_map_entries[pub["data_value"]] = str(result)
        continue

    try:
        ingest_publication(
            db,
            doi=doi,
            bibcode=bibcode,
            reference=reference,
            description=pub.get("description"),
            ignore_ads=IGNORE_ADS,
        )
        added += 1
        logger.info(f"Ingested: {label}")
        if pub.get("data_value"):
            shortname = stored_reference(db, reference, doi, bibcode)
            if shortname:
                new_map_entries[pub["data_value"]] = shortname
            else:
                logger.warning(f"Could not find the stored shortname for {label}; not added to the map")
    except Exception as e:
        failed += 1
        logger.warning(f"Failed to ingest {label}: {e}")

logger.info(
    f"Done: {added} added, {already_present} already present, {failed} failed "
    f"out of {len(PUBLICATIONS)} publications"
)

existing_map = None
if new_map_entries:
    logger.info(f"Reference map for this run: {new_map_entries}")
    existing_map = read_reference_map(new_map_entries)  # conflicts stop the run here, dry run included

if SAVE_DB:
    db.save_database()
    logger.info("Database saved.")
    if new_map_entries:
        update_reference_map(existing_map, new_map_entries)
else:
    logger.info(
        "Dry run complete — NOT saved. Set SAVE_DB = True to write the database to JSON files."
    )

# --- Batch helper: build PUBLICATIONS from a table column instead of hand-listing ---
# Replace the PUBLICATIONS literal above with something like:
#
#     from astropy.table import Table
#     data = Table.read("path/to/file.ecsv")     # auto-detects .fits/.csv/.ecsv
#     refs = sorted({str(r).strip() for r in data["reference"] if str(r).strip()})
#     PUBLICATIONS = [{"reference": r} for r in refs]   # bare shortnames -> IGNORE_ADS=True
#
# If the column holds DOIs instead, use {"doi": d} and an ADS token to auto-populate.
#
# Keep the table's own value as "data_value" so the reference map can be written, e.g.
# after resolving each one (Step 2):
#
#     PUBLICATIONS = [
#         {"data_value": "Bonaca2020", "reference": "Bona20", "doi": "10.3847/2041-8213/ab800c"},
#         ...
#     ]
