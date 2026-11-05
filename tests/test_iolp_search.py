"""Unit tests for the search_federal_properties MCP tool.

All tests run fully offline — HTTP is mocked at the requests.get boundary
inside src.clients.iolp_client so no network call is ever made.

Evidence checks covered:
  1. Both datasets searched → both source references in sources_attempted/retrieved
  2. Validation failure   → sources_attempted=[], fetch_attempted_at=None, retrieved_at_utc=None
  3. Invalid dataset value → Pydantic rejection before any HTTP call
  4. Row limit reached     → truncated=True, explicit partial-results message
  5. Happy path search     → results, correct provenance fields
  6. State/city filters    → correct filtering
  7. Empty result          → message, no crash
  8. Client error          → structured error dict, null provenance
  9. Missing column        → explicit schema error message
"""

import io
from unittest.mock import MagicMock, patch

import openpyxl
import pytest

from src.server import search_federal_properties
from src.clients.iolp_client import _MAX_ROWS

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


def _make_xlsx(headers: list, rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def _mock_response(
    content: bytes,
    status: int = 200,
    content_length: int | None = None,
) -> MagicMock:
    """Build a streaming-compatible mock response.

    Supports the new _fetch_xlsx interface:
    - allow_redirects=False  → status must not be 3xx (use status=200 or error)
    - stream=True            → iter_content(chunk_size=N) returns an iterator
    - resp.close()           → no-op MagicMock

    content_length: when set, adds a Content-Length header.
    """
    m = MagicMock()
    m.status_code = status
    m.headers = {}
    if content_length is not None:
        m.headers["Content-Length"] = str(content_length)
    # iter_content returns the body in a single chunk each time it is called.
    m.iter_content.side_effect = lambda chunk_size=65536: iter([content] if content else [b""])
    m.close = MagicMock()
    return m


def _leases_row(location_code="CT3414", name="2 UNION PLAZA", city="NEW LONDON",
                state="CT", lease_number="LCT00005") -> list:
    return [
        location_code, name, "N/A", "L", 1,
        "2 UNION PLZ", city, state, "06320-6309",
        41.354, -72.094, 7276, None, "0902", "JOE COURTNEY",
        lease_number, "7/27/2019", "7/26/2034", "BUILDING",
    ]


def _buildings_row(location_code="MA6048", name="ICE HSI BOSTON", city="BOSTON",
                   state="MA", status="ACTIVE", ownership="LEASED") -> list:
    return [
        location_code, name, "N/A", ownership, 1,
        "580 CHELSEA STREET", city, state, "02128-1566",
        42.384, -71.023, 4288, None, 2000, "2507",
        "AYANNA PRESSLEY", status, "BUILDING",
    ]


# ---------------------------------------------------------------------------
# Evidence 1: Both datasets searched → both sources in output
# ---------------------------------------------------------------------------

def test_both_datasets_searched_sources_preserved():
    """When dataset=None, both XLSX files are fetched and both URLs appear
    in sources_attempted and sources_retrieved."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row(name="UNION PLAZA")])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [_buildings_row(name="UNION STATION")])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = search_federal_properties(q="UNION")

    assert result["total_found"] == 2
    assert len(result["sources_attempted"]) == 2
    assert len(result["sources_retrieved"]) == 2
    assert result["sources_attempted"] == result["sources_retrieved"]
    # Both must be the actual XLSX URLs, not just the catalog page
    for url in result["sources_attempted"]:
        assert "inventory.data.gov" in url
    # retrieved_at_utc is non-null when all sources retrieved
    assert result["retrieved_at_utc"] is not None
    assert result["fetch_attempted_at"] is not None


# ---------------------------------------------------------------------------
# Evidence 2: Validation failure → null provenance, no HTTP call
# ---------------------------------------------------------------------------

def test_validation_failure_null_provenance():
    """Empty q must fail validation. No HTTP request is made and all
    retrieval-timestamp fields must be null."""
    with patch("src.clients.iolp_client.requests.get") as mock_get:
        result = search_federal_properties(q="")

    mock_get.assert_not_called()
    assert result["sources_attempted"] == []
    assert result["sources_retrieved"] == []
    assert result["fetch_attempted_at"] is None
    assert result["retrieved_at_utc"] is None
    assert result["message"] is not None


def test_validation_failure_limit_too_large():
    with patch("src.clients.iolp_client.requests.get") as mock_get:
        result = search_federal_properties(q="boston", limit=101)

    mock_get.assert_not_called()
    assert result["fetch_attempted_at"] is None
    assert result["retrieved_at_utc"] is None


# ---------------------------------------------------------------------------
# Evidence 3: Invalid dataset value → Pydantic rejection before HTTP
# ---------------------------------------------------------------------------

def test_invalid_dataset_value_rejected():
    """dataset='both' is not a valid Literal value; must be rejected by
    Pydantic without any HTTP call."""
    with patch("src.clients.iolp_client.requests.get") as mock_get:
        result = search_federal_properties(q="boston", dataset="both")

    mock_get.assert_not_called()
    assert result["fetch_attempted_at"] is None
    assert result["message"] is not None


def test_invalid_dataset_value_random_string():
    with patch("src.clients.iolp_client.requests.get") as mock_get:
        result = search_federal_properties(q="boston", dataset="all")

    mock_get.assert_not_called()
    assert result["message"] is not None


# ---------------------------------------------------------------------------
# Evidence 4: Row limit reached → truncated=True, explicit partial message
# ---------------------------------------------------------------------------

def test_row_limit_reached_sets_truncated_flag():
    """Simulate _MAX_ROWS being reached by patching _sheet_to_records to
    return truncated=True."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row(name="FEDERAL BUILDING")])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    from src.clients import iolp_client

    original = iolp_client._sheet_to_records

    call_count = [0]

    def patched_sheet_to_records(wb, dataset, required_cols):
        call_count[0] += 1
        records, _ = original(wb, dataset, required_cols)
        return records, True  # force truncated=True

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)), \
         patch("src.clients.iolp_client._sheet_to_records", side_effect=patched_sheet_to_records):
        result = search_federal_properties(q="FEDERAL")

    assert result["truncated"] is True
    assert result["row_limit"] is not None
    assert result["records_scanned"] is not None
    # Message must clearly indicate partial results
    assert result["message"] is not None
    msg = result["message"].lower()
    assert "partial" in msg or "limit" in msg or "scanned" in msg


# ---------------------------------------------------------------------------
# Evidence 5: Happy path — correct provenance fields
# ---------------------------------------------------------------------------

def test_happy_path_provenance_fields():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row(name="FEDERAL ANNEX")])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = search_federal_properties(q="FEDERAL")

    assert result["total_found"] == 1
    assert result["returned"] == 1
    assert result["source_url"].startswith("https://")
    assert result["fetch_attempted_at"] is not None
    assert result["retrieved_at_utc"] is not None
    assert "T" in result["retrieved_at_utc"]
    # row_limit and records_scanned reported
    assert result["row_limit"] is not None
    assert result["records_scanned"] is not None
    assert result["truncated"] is False
    assert result["message"] is None


# ---------------------------------------------------------------------------
# Evidence 6: State / city filter
# ---------------------------------------------------------------------------

def test_state_filter():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(name="MA OFFICE", city="BOSTON", state="MA"),
        _leases_row(location_code="VA001", name="VA OFFICE", city="ARLINGTON", state="VA"),
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([_mock_response(leases_xlsx), _mock_response(buildings_xlsx)])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = search_federal_properties(q="OFFICE", state="MA")

    assert result["total_found"] == 1
    assert result["results"][0]["state"] == "MA"


def test_city_filter_requires_q_match():
    """q and city are both required to match; the intersection can be empty."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(name="BOSTON ANNEX", city="BOSTON", state="MA"),
        _leases_row(location_code="CT001", name="NEW HAVEN OFFICE", city="NEW HAVEN", state="CT"),
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([_mock_response(leases_xlsx), _mock_response(buildings_xlsx)])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = search_federal_properties(q="OFFICE", city="BOSTON")

    assert result["total_found"] == 0


# ---------------------------------------------------------------------------
# Evidence 7: Empty result
# ---------------------------------------------------------------------------

def test_empty_result_message():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row(name="PENTAGON")])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([_mock_response(leases_xlsx), _mock_response(buildings_xlsx)])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = search_federal_properties(q="XYZNOTFOUND")

    assert result["total_found"] == 0
    assert result["message"] == "No matching federal properties found."
    # Even on empty result, retrieval timestamps must reflect what happened
    assert result["fetch_attempted_at"] is not None


# ---------------------------------------------------------------------------
# Evidence 8: Client error → structured error dict, null provenance
# ---------------------------------------------------------------------------

def test_client_error_null_provenance():
    from src.clients.iolp_client import IolpClientError

    with patch("src.clients.iolp_client.requests.get", side_effect=IolpClientError("network down")):
        result = search_federal_properties(q="anything")

    assert result["total_found"] == 0
    assert "network down" in result["message"]
    assert result["sources_attempted"] == []
    assert result["sources_retrieved"] == []
    assert result["fetch_attempted_at"] is None
    assert result["retrieved_at_utc"] is None


def test_non_200_response_null_provenance():
    import re
    with patch("src.clients.iolp_client.requests.get",
               return_value=_mock_response(b"", status=503)):
        result = search_federal_properties(q="anything")

    assert result["total_found"] == 0
    assert "503" in result["message"]
    # fetch_attempted_at must be a UTC ISO timestamp, not just truthy
    assert result["fetch_attempted_at"] is not None
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result["fetch_attempted_at"])
    assert result["retrieved_at_utc"] is None


# ---------------------------------------------------------------------------
# Evidence 9: Missing required column → explicit schema error
# ---------------------------------------------------------------------------

def test_missing_required_column_error():
    bad_headers = [h for h in LEASES_HEADERS if h != "Location Code"]
    bad_xlsx = _make_xlsx(bad_headers, [])

    with patch("src.clients.iolp_client.requests.get",
               return_value=_mock_response(bad_xlsx)):
        result = search_federal_properties(q="anything", dataset="leases")

    assert result["total_found"] == 0
    assert result["message"] is not None
    assert "Location Code" in result["message"]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_pagination_offset():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(location_code="A001", name="ALPHA BUILDING"),
        _leases_row(location_code="A002", name="ALPHA ANNEX"),
        _leases_row(location_code="A003", name="ALPHA SUITE"),
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([_mock_response(leases_xlsx), _mock_response(buildings_xlsx)])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = search_federal_properties(q="ALPHA", limit=2, offset=1)

    assert result["total_found"] == 3
    assert result["returned"] == 2
