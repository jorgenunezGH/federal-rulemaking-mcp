"""
Part B — Frozen-Snapshot Evaluation: S1, S2, S3-real, S4, S5-real, S6, S7.

Uses the exact frozen workbook bytes (SHA-256 verified against manifest) injected
at the HTTP boundary via unittest.mock.patch.  No live network calls are made.
The actual registered MCP tool functions are called directly.

Run with:
    .venv/bin/python evaluations/iolp/run_snapshot_eval.py

Prerequisites:
    evaluations/iolp/snapshots/iolp-leases.xlsx
    evaluations/iolp/snapshots/iolp-buildings.xlsx
    (SHA-256 verified against evaluations/iolp/snapshot-manifest.txt)
"""

import hashlib
import io
import json
import sys
from typing import Any
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

from src.clients.iolp_client import LEASES_URL, BUILDINGS_URL
from src.server import get_property_details, search_federal_properties

# ---------------------------------------------------------------------------
# Manifest constants (from evaluations/iolp/snapshot-manifest.txt)
# ---------------------------------------------------------------------------

MANIFEST = {
    "leases": {
        "path": "evaluations/iolp/snapshots/iolp-leases.xlsx",
        "sha256": "8d74c51668024199b22f2c2f9c7bb2745b7ca2af11bc416f8b48fe271048ef5c",
        "url": LEASES_URL,
        "rows": 7078,
    },
    "buildings": {
        "path": "evaluations/iolp/snapshots/iolp-buildings.xlsx",
        "sha256": "fb43d36814f1889982f5176b81346b88b8b3f92a9dd396f1cee4c3b5cb117faa",
        "url": BUILDINGS_URL,
        "rows": 7746,
    },
}

# ---------------------------------------------------------------------------
# Load and verify snapshot bytes
# ---------------------------------------------------------------------------

def _load_and_verify(name: str) -> bytes:
    m = MANIFEST[name]
    with open(m["path"], "rb") as fh:
        data = fh.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != m["sha256"]:
        raise RuntimeError(
            f"SHA-256 MISMATCH for {name}!\n"
            f"  expected: {m['sha256']}\n"
            f"  actual:   {actual}\n"
            "Snapshot integrity check failed — aborting evaluation."
        )
    print(f"[SHA-256 OK] {name}: {actual[:16]}…  ({len(data):,} bytes)")
    return data

print("=== Snapshot integrity verification ===")
LEASES_BYTES = _load_and_verify("leases")
BUILDINGS_BYTES = _load_and_verify("buildings")
print()

# ---------------------------------------------------------------------------
# Mock builder — mirrors _mock_response in test_iolp_hardening.py
# ---------------------------------------------------------------------------

def _mock_ok(content: bytes, url: str = "https://mocked/") -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.headers = {}
    m.url = url
    chunks = [content[i:i+65536] for i in range(0, len(content), 65536)] or [b""]
    m.iter_content.side_effect = lambda chunk_size=65536: iter(chunks)
    m.close = MagicMock()
    return m

def _mock_redirect(location: str, status: int = 302) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.headers = {"Location": location}
    m.close = MagicMock()
    return m

# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------

results: list[dict[str, Any]] = []
PASS_COUNT = 0
FAIL_COUNT = 0

def _check(label: str, actual: Any, expected: Any, notes: str = "") -> bool:
    if isinstance(expected, str) and expected == "__non_null__":
        ok = actual is not None
        reason = f"expected non-null, got None" if not ok else ""
    elif isinstance(expected, str) and expected == "__null__":
        ok = actual is None
        reason = f"expected null, got {actual!r}" if not ok else ""
    elif isinstance(expected, str) and expected.startswith("__contains__:"):
        substr = expected[len("__contains__:"):]
        ok = substr in (actual or "")
        reason = f"expected substring {substr!r} in {(actual or '')[:120]!r}" if not ok else ""
    else:
        ok = actual == expected
        reason = f"expected {expected!r}, got {actual!r}" if not ok else ""
    return ok, reason

def record_scenario(sid, category, tool, params, checks, observed, notes=""):
    global PASS_COUNT, FAIL_COUNT
    failures = []
    for field, expected in checks.items():
        actual = observed.get(field)
        ok, reason = _check(field, actual, expected)
        if not ok:
            failures.append(f"  ✗ {field}: {reason}")
    passed = len(failures) == 0
    if passed:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
    results.append({
        "id": sid,
        "category": category,
        "tool": tool,
        "params": params,
        "pass": passed,
        "failures": failures,
        "notes": notes,
        "observed": {k: observed.get(k) for k in checks},
    })

def _fmt(v):
    if isinstance(v, str) and len(v) > 90:
        return v[:90] + "…"
    return v

# ---------------------------------------------------------------------------
# S1 — Lease record with explicit dataset= pin (CT3414, dataset="leases")
# Expected: leases row returned, not the buildings row
# Source: workbook row CT3414, leases dataset (independent inspection)
# ---------------------------------------------------------------------------

print("Running S1 — CT3414 dataset=leases …")
# Inject frozen leases bytes via GovCloud-style redirect (mirrors production flow)
govcloud_redirect = _mock_redirect(
    "https://s3-us-gov-west-1.amazonaws.com/cg-ae4ac6ad/iolp-leases.xlsx"
)
leases_200 = _mock_ok(LEASES_BYTES, "https://s3-us-gov-west-1.amazonaws.com/cg-ae4ac6ad/iolp-leases.xlsx")
responses_s1 = iter([govcloud_redirect, leases_200])
with patch("src.clients.iolp_client.requests.get",
           side_effect=lambda *a, **kw: next(responses_s1)):
    obs_s1 = get_property_details(location_code="CT3414", dataset="leases")

record_scenario(
    "S1", "Lease record — explicit dataset pin", "get_property_details",
    {"location_code": "CT3414", "dataset": "leases"},
    {
        "location_code": "CT3414",
        "real_property_asset_name": "2 UNION PLAZA",
        "street_address": "2 UNION PLZ",
        "city": "NEW LONDON",
        "state": "CT",
        "zip_code": "06320-6309",
        "lease_number": "LCT00005",
        "lease_expiration_date": "7/26/2034",
        "gsa_region": 1,
        "dataset": "leases",
        "source_file": LEASES_URL,
        "source_sha256": MANIFEST["leases"]["sha256"],
        "retrieved_at_utc": "__non_null__",
        "message": "__null__",
    },
    obs_s1,
    notes="CT3414 also in buildings; dataset=leases pins to leases record. GovCloud redirect exercised.",
)

# ---------------------------------------------------------------------------
# S2 — Buildings-only record (AK0001, dataset="buildings")
# Expected: buildings row returned; absent from leases
# Source: workbook row AK0001, buildings dataset (independent inspection)
# ---------------------------------------------------------------------------

print("Running S2 — AK0001 dataset=buildings …")
govcloud_redirect_b = _mock_redirect(
    "https://s3-us-gov-west-1.amazonaws.com/cg-ae4ac6ad/iolp-buildings.xlsx"
)
buildings_200 = _mock_ok(BUILDINGS_BYTES, "https://s3-us-gov-west-1.amazonaws.com/cg-ae4ac6ad/iolp-buildings.xlsx")
responses_s2 = iter([govcloud_redirect_b, buildings_200])
with patch("src.clients.iolp_client.requests.get",
           side_effect=lambda *a, **kw: next(responses_s2)):
    obs_s2 = get_property_details(location_code="AK0001", dataset="buildings")

record_scenario(
    "S2", "Buildings-only record", "get_property_details",
    {"location_code": "AK0001", "dataset": "buildings"},
    {
        "location_code": "AK0001",
        "real_property_asset_name": "ANCHORAGE HISTORIC FEDERAL BUILDING",
        "installation_name": "ANCHORAGE FEDERAL",
        "street_address": "605 W 4TH AVE",
        "city": "ANCHORAGE",
        "state": "AK",
        "zip_code": "99501-2248",
        "owned_or_leased": "OWNED",
        "building_status": "ACTIVE",
        "construction_date": 1939,
        "gsa_region": 10,
        "building_rentable_sq_ft": 91737.92,
        "dataset": "buildings",
        "source_file": BUILDINGS_URL,
        "source_sha256": MANIFEST["buildings"]["sha256"],
        "retrieved_at_utc": "__non_null__",
        "message": "__null__",
    },
    obs_s2,
    notes="AK0001 absent from leases (1,667 buildings-only codes). GovCloud redirect exercised.",
)

# ---------------------------------------------------------------------------
# S3-real — Cross-dataset ambiguity: CT3414 without dataset=
# Expected: ambiguity response, source_file=null, source_sha256=null
# ---------------------------------------------------------------------------

print("Running S3-real — CT3414 no dataset (cross-dataset ambiguity) …")
govcloud_l = _mock_redirect("https://s3-us-gov-west-1.amazonaws.com/cg/iolp-leases.xlsx")
leases_200b = _mock_ok(LEASES_BYTES)
govcloud_b = _mock_redirect("https://s3-us-gov-west-1.amazonaws.com/cg/iolp-buildings.xlsx")
buildings_200b = _mock_ok(BUILDINGS_BYTES)
responses_s3 = iter([govcloud_l, leases_200b, govcloud_b, buildings_200b])
with patch("src.clients.iolp_client.requests.get",
           side_effect=lambda *a, **kw: next(responses_s3)):
    obs_s3 = get_property_details(location_code="CT3414")

record_scenario(
    "S3-real", "Cross-dataset ambiguity (GSA-snapshot)", "get_property_details",
    {"location_code": "CT3414"},
    {
        "dataset": "unknown",
        "source_file": "__null__",
        "source_sha256": "__null__",
        "retrieved_at_utc": "__null__",
        "fetch_attempted_at": "__non_null__",
        "message": "__contains__:found in both datasets",
    },
    obs_s3,
    notes=(
        "CT3414 in both datasets. source_file=null by design (cross-dataset gap, §9.1). "
        "Disambiguation requires dataset= parameter."
    ),
)
# Check full message content
for substr in ["leases", "buildings", "dataset="]:
    if substr not in (obs_s3.get("message") or ""):
        results[-1]["failures"].append(f"  ✗ message missing substring {substr!r}")
        results[-1]["pass"] = False
        PASS_COUNT -= 1
        FAIL_COUNT += 1

# ---------------------------------------------------------------------------
# S4 — Definite absence (ZZZZ-EVAL-ABSENT-9999)
# Expected: not-found, source_file=BUILDINGS_URL, sha256=buildings manifest
# Note: source_file is BUILDINGS_URL because buildings is the last dataset searched
# ---------------------------------------------------------------------------

print("Running S4 — ZZZZ-EVAL-ABSENT-9999 (confirmed absent from snapshot) …")
govcloud_l2 = _mock_redirect("https://s3-us-gov-west-1.amazonaws.com/cg/iolp-leases.xlsx")
leases_200c = _mock_ok(LEASES_BYTES)
govcloud_b2 = _mock_redirect("https://s3-us-gov-west-1.amazonaws.com/cg/iolp-buildings.xlsx")
buildings_200c = _mock_ok(BUILDINGS_BYTES)
responses_s4 = iter([govcloud_l2, leases_200c, govcloud_b2, buildings_200c])
with patch("src.clients.iolp_client.requests.get",
           side_effect=lambda *a, **kw: next(responses_s4)):
    obs_s4 = get_property_details(location_code="ZZZZ-EVAL-ABSENT-9999")

record_scenario(
    "S4", "Definite absence", "get_property_details",
    {"location_code": "ZZZZ-EVAL-ABSENT-9999"},
    {
        "dataset": "unknown",
        "source_file": BUILDINGS_URL,
        "source_sha256": MANIFEST["buildings"]["sha256"],
        "retrieved_at_utc": "__null__",
        "fetch_attempted_at": "__non_null__",
        "message": "__contains__:No IOLP property found with Location Code",
    },
    obs_s4,
    notes=(
        "Code confirmed absent from both datasets via independent inspection. "
        "source_file=BUILDINGS_URL per iolp_client.py:924 (last retrieved source). "
        "NOTE: S4 exposes the known cross-dataset provenance limitation — "
        "source_sha256 = buildings hash even though leases was also searched."
    ),
)
if "leases and buildings" not in (obs_s4.get("message") or ""):
    results[-1]["failures"].append("  ✗ message missing 'leases and buildings'")
    results[-1]["pass"] = False

# ---------------------------------------------------------------------------
# S5-real — Intra-dataset ambiguity: CT3360, dataset="leases"
# Expected: 3 rows, lease numbers LCT00250, LCT00349, LCT04792 in message
# ---------------------------------------------------------------------------

print("Running S5-real — CT3360 dataset=leases (3 lease rows) …")
govcloud_s5 = _mock_redirect("https://s3-us-gov-west-1.amazonaws.com/cg/iolp-leases.xlsx")
leases_200d = _mock_ok(LEASES_BYTES)
responses_s5 = iter([govcloud_s5, leases_200d])
with patch("src.clients.iolp_client.requests.get",
           side_effect=lambda *a, **kw: next(responses_s5)):
    obs_s5 = get_property_details(location_code="CT3360", dataset="leases")

record_scenario(
    "S5-real", "Intra-dataset ambiguity (GSA-snapshot, CT3360)", "get_property_details",
    {"location_code": "CT3360", "dataset": "leases"},
    {
        "dataset": "unknown",
        "source_file": LEASES_URL,
        "source_sha256": MANIFEST["leases"]["sha256"],
        "retrieved_at_utc": "__null__",
        "fetch_attempted_at": "__non_null__",
        "message": "__contains__:matched 3 rows in the leases dataset",
    },
    obs_s5,
    notes="CT3360 has 3 lease rows: LCT00250, LCT00349, LCT04792 (independent inspection).",
)
for ln in ["LCT00250", "LCT00349", "LCT04792"]:
    if ln not in (obs_s5.get("message") or ""):
        results[-1]["failures"].append(f"  ✗ message missing lease number {ln!r}")
        results[-1]["pass"] = False
if "Supply a lease_number" not in (obs_s5.get("message") or ""):
    results[-1]["failures"].append("  ✗ message missing 'Supply a lease_number' hint")
    results[-1]["pass"] = False

# ---------------------------------------------------------------------------
# S6 — State filter: q="OFFICE", state="CA", dataset="leases"
# Expected: total_found=17, all results state="CA", sources_sha256=[leases hash]
# ---------------------------------------------------------------------------

print("Running S6 — search q=OFFICE state=CA dataset=leases …")
govcloud_s6 = _mock_redirect("https://s3-us-gov-west-1.amazonaws.com/cg/iolp-leases.xlsx")
leases_200e = _mock_ok(LEASES_BYTES)
responses_s6 = iter([govcloud_s6, leases_200e])
with patch("src.clients.iolp_client.requests.get",
           side_effect=lambda *a, **kw: next(responses_s6)):
    obs_s6 = search_federal_properties(q="OFFICE", state="CA", dataset="leases")

# Verify all returned records have state=CA
all_ca = all(
    (r.get("state") if isinstance(r, dict) else r.state) == "CA"
    for r in obs_s6.get("results", [])
)
record_scenario(
    "S6", "Search — state filter", "search_federal_properties",
    {"q": "OFFICE", "state": "CA", "dataset": "leases"},
    {
        "total_found": 17,
        "returned": 10,
        "sources_attempted": [LEASES_URL],
        "sources_retrieved": [LEASES_URL],
        "retrieved_at_utc": "__non_null__",
        "truncated": False,
        "message": "__null__",
    },
    obs_s6,
    notes=f"Expected 17 CA OFFICE leases (independent inspection). All results CA: {all_ca}.",
)
# SHA-256 check
sha_list = obs_s6.get("sources_sha256", [])
if not sha_list or sha_list[0] != MANIFEST["leases"]["sha256"]:
    results[-1]["failures"].append(
        f"  ✗ sources_sha256[0]={sha_list[0] if sha_list else 'MISSING'!r}, "
        f"expected {MANIFEST['leases']['sha256'][:16]}…"
    )
    results[-1]["pass"] = False
if not all_ca:
    results[-1]["failures"].append("  ✗ Not all returned records have state='CA'")
    results[-1]["pass"] = False

# ---------------------------------------------------------------------------
# S7 — City filter: q="BUILDING", city="WASHINGTON", dataset="buildings"
# Expected: total_found=37 (corrected: 35 via asset name + 2 via Installation Name)
# Prior inspection counted only Real Property Asset Name; _matches_query also
# searches Installation Name — 2 records match via "RONALD REAGAN BUILDING COMPLEX".
# ---------------------------------------------------------------------------

print("Running S7 — search q=BUILDING city=WASHINGTON dataset=buildings …")
govcloud_s7 = _mock_redirect("https://s3-us-gov-west-1.amazonaws.com/cg/iolp-buildings.xlsx")
buildings_200d = _mock_ok(BUILDINGS_BYTES)
responses_s7 = iter([govcloud_s7, buildings_200d])
with patch("src.clients.iolp_client.requests.get",
           side_effect=lambda *a, **kw: next(responses_s7)):
    obs_s7 = search_federal_properties(q="BUILDING", city="WASHINGTON", dataset="buildings")

all_washington = all(
    (r.get("city") if isinstance(r, dict) else r.city) == "WASHINGTON"
    for r in obs_s7.get("results", [])
)
record_scenario(
    "S7", "Search — city filter", "search_federal_properties",
    {"q": "BUILDING", "city": "WASHINGTON", "dataset": "buildings"},
    {
        "total_found": 37,
        "returned": 10,
        "sources_attempted": [BUILDINGS_URL],
        "sources_retrieved": [BUILDINGS_URL],
        "retrieved_at_utc": "__non_null__",
        "truncated": False,
        "message": "__null__",
    },
    obs_s7,
    notes=(
        "Corrected: 37 total (35 via Real Property Asset Name + 2 via Installation Name: "
        f"RONALD REAGAN BUILDING COMPLEX). All results WASHINGTON: {all_washington}."
    ),
)
sha_list_b = obs_s7.get("sources_sha256", [])
if not sha_list_b or sha_list_b[0] != MANIFEST["buildings"]["sha256"]:
    results[-1]["failures"].append(
        f"  ✗ sources_sha256[0]={sha_list_b[0] if sha_list_b else 'MISSING'!r}, "
        f"expected {MANIFEST['buildings']['sha256'][:16]}…"
    )
    results[-1]["pass"] = False
if not all_washington:
    results[-1]["failures"].append("  ✗ Not all returned records have city='WASHINGTON'")
    results[-1]["pass"] = False

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

print()
print("=" * 72)
print("PART B — FROZEN-SNAPSHOT EVALUATION RESULTS")
print("=" * 72)
print()

for r in results:
    status = "PASS" if r["pass"] else "FAIL"
    print(f"[{status}] {r['id']} — {r['category']}")
    print(f"       Tool: {r['tool']}")
    print(f"       Params: {json.dumps(r['params'])}")
    print()
    print("       Observed:")
    for k, v in r["observed"].items():
        print(f"         {k}: {_fmt(v)!r}")
    if r["notes"]:
        print(f"       Notes: {r['notes']}")
    if r["failures"]:
        print("       FAILURES:")
        for f in r["failures"]:
            print(f"       {f}")
    print()
    print("-" * 72)
    print()

print(f"Snapshot scenarios: {PASS_COUNT} PASS, {FAIL_COUNT} FAIL")
print()

# ---------------------------------------------------------------------------
# Write evidence JSON
# ---------------------------------------------------------------------------

evidence = {
    "gate": "B-snapshot",
    "description": "Frozen-snapshot evaluation S1/S2/S3-real/S4/S5-real/S6/S7",
    "snapshot_manifest": {
        "leases_sha256": MANIFEST["leases"]["sha256"],
        "buildings_sha256": MANIFEST["buildings"]["sha256"],
        "integrity": "VERIFIED",
    },
    "pass_count": PASS_COUNT,
    "fail_count": FAIL_COUNT,
    "scenarios": results,
}
with open("evaluations/iolp/gate_b_snapshot_results.json", "w") as fh:
    json.dump(evidence, fh, indent=2, default=str)
print("Evidence written to: evaluations/iolp/gate_b_snapshot_results.json")
