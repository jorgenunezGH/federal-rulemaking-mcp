"""
Gate B — Synthetic evaluation for scenarios S3, S5, S8, S9.

Each scenario uses openpyxl to build a minimal in-memory XLSX workbook,
wraps it in a mock HTTP response (identical to the _mock_response helper
used in the 153-test offline suite), and calls the actual MCP server tool
functions search_federal_properties / get_property_details.

No live network traffic is made.  All assertions check public MCP response
fields (model_dump() output), not internal exception attributes or private
dict keys.

Run with:
    .venv/bin/python evaluations/iolp/run_synthetic_eval.py
"""

import hashlib
import io
import json
import sys
import textwrap
from typing import Any
from unittest.mock import MagicMock, patch

import openpyxl

# Ensure src/ is importable
sys.path.insert(0, ".")

from src.clients.iolp_client import (
    LEASES_URL,
    BUILDINGS_URL,
    _MAX_ROWS,
)
from src.server import search_federal_properties, get_property_details

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LEASES_HEADERS = [
    "Location Code", "Real Property Asset Name", "Installation Name",
    "Federal Leased Code", "GSA Region", "Street Address", "City", "State",
    "Zip Code", "Latitude", "Longitude", "Building Rentable Square Feet",
    "Available Square Feet", "Congressional District",
    "Congressional District Representative", "Lease Number",
    "Lease Effective Date", "Lease Expiration Date", "Real Property Asset type",
]

BUILDINGS_HEADERS = [
    "Location Code", "Real Property Asset Name", "Installation Name",
    "Owned or Leased", "GSA Region", "Street Address", "City", "State",
    "Zip Code", "Latitude", "Longitude", "Building Rentable Square Feet",
    "Available Square Feet", "Construction Date", "Congressional District",
    "Congressional District Representative Name", "Building Status",
    "Real Property Asset Type",
]


def _make_xlsx(headers: list, rows: list) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mock_response(content: bytes, status: int = 200) -> MagicMock:
    """Build a streaming-compatible mock matching the _fetch_xlsx interface."""
    m = MagicMock()
    m.status_code = status
    m.headers = {}
    m.url = "https://inventory.data.gov/MOCKED"
    chunks = [content[i:i + 65536] for i in range(0, len(content), 65536)] or [b""]
    m.iter_content.return_value = iter(chunks)
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    m.close = MagicMock()
    return m


def _make_leases_row(location_code: str, lease_number: str, asset_name: str = "TEST ASSET") -> list:
    """Return a single data row for LEASES_HEADERS in column order."""
    row = [""] * len(LEASES_HEADERS)
    idx = {h: i for i, h in enumerate(LEASES_HEADERS)}
    row[idx["Location Code"]] = location_code
    row[idx["Real Property Asset Name"]] = asset_name
    row[idx["Installation Name"]] = "INSTALL"
    row[idx["GSA Region"]] = 1
    row[idx["Street Address"]] = "100 MAIN ST"
    row[idx["City"]] = "TESTCITY"
    row[idx["State"]] = "VA"
    row[idx["Zip Code"]] = "20001"
    row[idx["Lease Number"]] = lease_number
    return row


def _make_buildings_row(location_code: str, asset_name: str = "TEST BLDG",
                        building_status: str = "ACTIVE") -> list:
    """Return a single data row for BUILDINGS_HEADERS in column order."""
    row = [""] * len(BUILDINGS_HEADERS)
    idx = {h: i for i, h in enumerate(BUILDINGS_HEADERS)}
    row[idx["Location Code"]] = location_code
    row[idx["Real Property Asset Name"]] = asset_name
    row[idx["GSA Region"]] = 1
    row[idx["Street Address"]] = "200 FED AVE"
    row[idx["City"]] = "GOVTOWN"
    row[idx["State"]] = "DC"
    row[idx["Zip Code"]] = "20500"
    row[idx["Building Status"]] = building_status
    row[idx["Owned or Leased"]] = "OWNED"
    return row


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------

results: list[dict[str, Any]] = []

def record(scenario_id: str, category: str, tool: str, params: dict,
           expected: dict, observed: dict, notes: str = "") -> None:
    # Determine pass/fail for each expected key
    failures = []
    for key, exp_val in expected.items():
        if exp_val == "__non_null__":
            if observed.get(key) is None:
                failures.append(f"{key}: expected non-null, got None")
        elif exp_val == "__null__":
            if observed.get(key) is not None:
                failures.append(f"{key}: expected null, got {observed.get(key)!r}")
        elif exp_val == "__true__":
            if observed.get(key) is not True:
                failures.append(f"{key}: expected True, got {observed.get(key)!r}")
        elif isinstance(exp_val, str) and exp_val.startswith("__contains__:"):
            substr = exp_val[len("__contains__:"):]
            actual = observed.get(key) or ""
            if substr not in actual:
                failures.append(f"{key}: expected to contain {substr!r}, got {actual!r}")
        elif isinstance(exp_val, str) and exp_val.startswith("__len_ge__:"):
            n = int(exp_val[len("__len_ge__:"):])
            actual = observed.get(key)
            if not (isinstance(actual, list) and len(actual) >= n):
                failures.append(f"{key}: expected list len >= {n}, got {actual!r}")
        else:
            if observed.get(key) != exp_val:
                failures.append(f"{key}: expected {exp_val!r}, got {observed.get(key)!r}")

    passed = len(failures) == 0
    results.append({
        "id": scenario_id,
        "category": category,
        "tool": tool,
        "params": params,
        "pass": passed,
        "failures": failures,
        "notes": notes,
        "observed_keys": {k: observed.get(k) for k in expected},
    })


# ---------------------------------------------------------------------------
# S3 — Cross-Dataset Ambiguity (Synthetic)
# ---------------------------------------------------------------------------

leases_bytes_s3 = _make_xlsx(LEASES_HEADERS, [
    _make_leases_row("EVAL-XDATASET-01", "EVAL-LN-S3"),
])
buildings_bytes_s3 = _make_xlsx(BUILDINGS_HEADERS, [
    _make_buildings_row("EVAL-XDATASET-01"),
])

with patch("requests.get") as mock_get:
    mock_get.side_effect = [
        _mock_response(leases_bytes_s3),
        _mock_response(buildings_bytes_s3),
    ]
    obs_s3 = get_property_details(location_code="EVAL-XDATASET-01")

record(
    "S3", "Cross-dataset ambiguity (synthetic)", "get_property_details",
    {"location_code": "EVAL-XDATASET-01"},
    {
        "dataset": "unknown",
        "source_file": "__null__",
        "source_sha256": "__null__",
        "retrieved_at_utc": "__null__",
        "fetch_attempted_at": "__non_null__",
        "message": "__contains__:found in both datasets",
    },
    obs_s3,
    notes="source_file/source_sha256 intentionally null per design (iolp_client.py:896-905)",
)

# Additional message substring checks
for substr in ["leases", "buildings", "dataset="]:
    msg = obs_s3.get("message") or ""
    if substr not in msg:
        results[-1]["failures"].append(f"message missing substring {substr!r}")
        results[-1]["pass"] = False


# ---------------------------------------------------------------------------
# S5 — Intra-Dataset Ambiguity (Synthetic)
# ---------------------------------------------------------------------------

leases_bytes_s5 = _make_xlsx(LEASES_HEADERS, [
    _make_leases_row("EVAL-MULTI-LEASE-01", "EVAL-LN-001"),
    _make_leases_row("EVAL-MULTI-LEASE-01", "EVAL-LN-002"),
])
s5_leases_sha256 = _sha256(leases_bytes_s5)

with patch("requests.get") as mock_get:
    mock_get.return_value = _mock_response(leases_bytes_s5)
    obs_s5 = get_property_details(
        location_code="EVAL-MULTI-LEASE-01", dataset="leases"
    )

record(
    "S5", "Intra-dataset ambiguity (synthetic)", "get_property_details",
    {"location_code": "EVAL-MULTI-LEASE-01", "dataset": "leases"},
    {
        "dataset": "unknown",
        "source_file": LEASES_URL,
        "source_sha256": s5_leases_sha256,
        "retrieved_at_utc": "__null__",
        "fetch_attempted_at": "__non_null__",
        "message": "__contains__:matched 2 rows in the leases dataset",
    },
    obs_s5,
    notes=f"fixture SHA-256: {s5_leases_sha256[:16]}…",
)
# Check both lease numbers appear in message
for ln in ["EVAL-LN-001", "EVAL-LN-002"]:
    msg = obs_s5.get("message") or ""
    if ln not in msg:
        results[-1]["failures"].append(f"message missing lease number {ln!r}")
        results[-1]["pass"] = False
# Check "Supply a lease_number" hint
if "Supply a lease_number" not in (obs_s5.get("message") or ""):
    results[-1]["failures"].append("message missing 'Supply a lease_number' hint")
    results[-1]["pass"] = False


# ---------------------------------------------------------------------------
# S8 — Truncated Scan Disclosure (Synthetic)
# ---------------------------------------------------------------------------
# Build a leases XLSX with _MAX_ROWS + 1 data rows; all contain "TRUNCTEST"

truncation_rows = [
    _make_leases_row(f"TRUNC-{i:05d}", f"LN-{i:05d}", "TRUNCTEST FEDERAL BLDG")
    for i in range(_MAX_ROWS + 1)
]
leases_bytes_s8 = _make_xlsx(LEASES_HEADERS, truncation_rows)

with patch("requests.get") as mock_get:
    mock_get.return_value = _mock_response(leases_bytes_s8)
    obs_s8 = search_federal_properties(q="TRUNCTEST", dataset="leases")

record(
    "S8", "Truncated scan disclosure (synthetic)", "search_federal_properties",
    {"q": "TRUNCTEST", "dataset": "leases"},
    {
        "truncated": "__true__",
        "row_limit": _MAX_ROWS,
        "records_scanned": _MAX_ROWS,
        "sources_retrieved": "__len_ge__:1",
        "retrieved_at_utc": "__non_null__",
        "message": "__contains__:Partial results",
    },
    obs_s8,
    notes=f"fixture has {_MAX_ROWS + 1} data rows; cap is {_MAX_ROWS}",
)
if "50,000" not in (obs_s8.get("message") or ""):
    results[-1]["failures"].append("message missing '50,000' row-limit figure")
    results[-1]["pass"] = False


# ---------------------------------------------------------------------------
# S9 — Buildings-Only Filter Passthrough (Synthetic)
# ---------------------------------------------------------------------------
# One leases row (no Building Status) + one buildings row (INACTIVE).
# Both contain "FEDERAL" in asset name.
# building_status="ACTIVE" filter:
#   - buildings INACTIVE row should be excluded
#   - leases row should pass through (documented passthrough, not filtering error)

leases_bytes_s9 = _make_xlsx(LEASES_HEADERS, [
    _make_leases_row("EVAL-S9-LEASE", "LN-S9", "FEDERAL OFFICE LEASE"),
])
buildings_bytes_s9 = _make_xlsx(BUILDINGS_HEADERS, [
    _make_buildings_row("EVAL-S9-BLDG", "FEDERAL OFFICE BLDG", building_status="INACTIVE"),
])

# NOTE: dataset="both" is rejected by IolpSearchInput (Literal["leases","buildings"]).
# Omit dataset= to search both; "both" is the internal default when dataset is None.
with patch("requests.get") as mock_get:
    mock_get.side_effect = [
        _mock_response(leases_bytes_s9),
        _mock_response(buildings_bytes_s9),
    ]
    obs_s9 = search_federal_properties(
        q="FEDERAL", building_status="ACTIVE"  # dataset omitted → both
    )

# The expected behaviour:
#   total_found = 1 (leases row only; INACTIVE buildings row excluded)
#   results[0].dataset = "leases"
#   The buildings INACTIVE row is absent — correct filter enforcement for buildings
#   The leases row is present — documented passthrough (not a filtering success)

record(
    "S9", "Buildings-only filter passthrough (synthetic)", "search_federal_properties",
    {"q": "FEDERAL", "building_status": "ACTIVE", "dataset": "(omitted = both)"},
    {
        "total_found": 1,
    },
    obs_s9,
    notes=(
        "Leases row passes through unconditionally (iolp_client.py:715-719). "
        "DOCUMENTED PASSTHROUGH behavior, not correct filter enforcement. "
        "INACTIVE buildings row correctly excluded. "
        "PLAN FIX: dataset='both' invalid; omit dataset= to search both datasets."
    ),
)
# Verify the returned record is the leases row
if obs_s9.get("total_found") == 1:
    returned_records = obs_s9.get("results", [])
    if returned_records:
        rec0 = returned_records[0] if isinstance(returned_records[0], dict) else returned_records[0].model_dump() if hasattr(returned_records[0], 'model_dump') else {}
        if rec0.get("dataset") != "leases":
            results[-1]["failures"].append(
                f"Expected returned record dataset='leases', got {rec0.get('dataset')!r}"
            )
            results[-1]["pass"] = False


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

print("=" * 72)
print("GATE B — SYNTHETIC EVALUATION RESULTS")
print("=" * 72)
print()

all_pass = True
for r in results:
    status = "PASS" if r["pass"] else "FAIL"
    if not r["pass"]:
        all_pass = False
    print(f"[{status}] {r['id']} — {r['category']}")
    print(f"       Tool:   {r['tool']}")
    print(f"       Params: {json.dumps(r['params'])}")
    if r["notes"]:
        print(f"       Notes:  {r['notes']}")
    print()
    print("       Observed public response fields:")
    for k, v in r["observed_keys"].items():
        if isinstance(v, str) and len(v) > 80:
            v = v[:80] + "…"
        print(f"         {k}: {v!r}")
    if r["failures"]:
        print()
        print("       FAILURES:")
        for f in r["failures"]:
            print(f"         ✗ {f}")
    print()
    print("-" * 72)
    print()

print()
print(f"Overall Gate B result: {'ALL PASS' if all_pass else 'SOME FAILURES'}")
print()

# Write JSON evidence file
evidence = {
    "gate": "B",
    "description": "Synthetic evaluation S3/S5/S8/S9",
    "all_pass": all_pass,
    "scenarios": results,
}
with open("evaluations/iolp/gate_b_results.json", "w") as fh:
    json.dump(evidence, fh, indent=2, default=str)
print("Evidence written to: evaluations/iolp/gate_b_results.json")
