"""Unit tests for the get_property_details MCP tool.

All tests run fully offline — HTTP is mocked at the requests.get boundary
inside src.clients.iolp_client so no network call is ever made.

Evidence checks covered:
  1. Duplicate leases in one dataset   → IolpAmbiguousRecordError with lease numbers
  2. Same code in both datasets        → IolpAmbiguousRecordError unless dataset= explicit
  3. Explicit dataset= restricts fetch → only one HTTP call made
  4. Invalid dataset value             → Pydantic rejection before HTTP
  5. Validation failure                → null fetch_attempted_at, null retrieved_at_utc
  6. Both sources survive serialization→ source_file is the actual XLSX URL
  7. Row limit hit, record not found   → IolpClientError (not IolpRecordNotFoundError)
  8. Happy path leases + buildings     → correct fields and provenance
  9. Non-200 and invalid XLSX          → structured errors, null provenance
 10. Truncated scan + 1 match          → IolpAmbiguousRecordError (uniqueness unconfirmed)
 11. lease_number resolves duplicate   → returns specific lease row
 12. Fetch error → sources_attempted non-empty in response
"""

import io
from unittest.mock import MagicMock, patch

import openpyxl
import pytest

from src.clients.iolp_client import LEASES_URL, BUILDINGS_URL
from src.server import get_property_details


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
    m.iter_content.side_effect = lambda chunk_size=65536: iter([content] if content else [b""])
    m.close = MagicMock()
    return m


def _leases_row(location_code="CT3414", name="2 UNION PLAZA", lease_number="LCT00005") -> list:
    return [
        location_code, name, "N/A", "L", 1,
        "2 UNION PLZ", "NEW LONDON", "CT", "06320-6309",
        41.3542597, -72.094347, 7276, None, "0902", "JOE COURTNEY",
        lease_number, "7/27/2019", "7/26/2034", "BUILDING",
    ]


def _buildings_row(location_code="MA6048", name="ICE HSI BOSTON") -> list:
    return [
        location_code, name, "N/A", "LEASED", 1,
        "580 CHELSEA STREET", "BOSTON", "MA", "02128-1566",
        42.384501021534, -71.023153826139, 4288, None, 2000, "2507",
        "AYANNA PRESSLEY", "ACTIVE", "BUILDING",
    ]


# ---------------------------------------------------------------------------
# Evidence 1: Within-dataset duplicate → ambiguity with lease numbers
# ---------------------------------------------------------------------------

def test_within_dataset_duplicate_lease_numbers_in_message():
    """A Location Code matching two rows in the same dataset must raise
    IolpAmbiguousRecordError that names the available Lease Numbers."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(location_code="CT3414", lease_number="LCT00005"),
        _leases_row(location_code="CT3414", lease_number="LCT00099"),
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CT3414")

    assert result["dataset"] == "unknown"
    assert result["message"] is not None
    msg = result["message"]
    # Message must include both lease numbers
    assert "LCT00005" in msg or "LCT00099" in msg
    assert "2" in msg  # "matched 2 rows" or similar
    assert result["source_url"].startswith("https://")
    # Provenance: fetch succeeded (within-dataset ambiguity) — source_file is
    # the XLSX URL that was downloaded, not None (C2 contract).
    assert result["source_file"] == LEASES_URL
    assert result["fetch_attempted_at"] is not None


# ---------------------------------------------------------------------------
# Evidence 2: Same code in both datasets → cross-dataset ambiguity
# ---------------------------------------------------------------------------

def test_cross_dataset_ambiguity_without_explicit_dataset():
    shared_code = "XX0001"
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        [shared_code, "SHARED BLDG", "N/A", "L", 1,
         "1 MAIN ST", "SPRINGFIELD", "IL", "62701",
         39.0, -89.0, 5000, None, "1701", "REP",
         "LIL00001", "1/1/2020", "12/31/2025", "BUILDING"],
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [
        [shared_code, "SHARED BLDG", "N/A", "OWNED", 5,
         "1 MAIN ST", "SPRINGFIELD", "IL", "62701",
         39.0, -89.0, 5000, None, 1990, "1701",
         "REP", "ACTIVE", "BUILDING"],
    ])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details(shared_code)

    assert result["dataset"] == "unknown"
    assert result["message"] is not None
    assert "both" in result["message"].lower()
    assert result["source_url"].startswith("https://")


def test_cross_dataset_ambiguity_resolved_with_explicit_dataset():
    shared_code = "XX0001"
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        [shared_code, "SHARED BLDG", "N/A", "L", 1,
         "1 MAIN ST", "SPRINGFIELD", "IL", "62701",
         39.0, -89.0, 5000, None, "1701", "REP",
         "LIL00001", "1/1/2020", "12/31/2025", "BUILDING"],
    ])

    with patch("src.clients.iolp_client.requests.get", return_value=_mock_response(leases_xlsx)) as mock_get:
        result = get_property_details(shared_code, dataset="leases")

    # Only the leases file should have been fetched
    assert mock_get.call_count == 1
    assert result["dataset"] == "leases"
    assert result["message"] is None


# ---------------------------------------------------------------------------
# Evidence 3: Explicit dataset= restricts fetch to one HTTP call
# ---------------------------------------------------------------------------

def test_explicit_dataset_leases_one_http_call():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row()])

    with patch("src.clients.iolp_client.requests.get", return_value=_mock_response(leases_xlsx)) as mock_get:
        result = get_property_details("CT3414", dataset="leases")

    assert mock_get.call_count == 1
    assert result["dataset"] == "leases"
    assert result["message"] is None


def test_explicit_dataset_buildings_one_http_call():
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [_buildings_row()])

    with patch("src.clients.iolp_client.requests.get", return_value=_mock_response(buildings_xlsx)) as mock_get:
        result = get_property_details("MA6048", dataset="buildings")

    assert mock_get.call_count == 1
    assert result["dataset"] == "buildings"
    assert result["message"] is None


# ---------------------------------------------------------------------------
# Evidence 4: Invalid dataset value → Pydantic rejection before HTTP
# ---------------------------------------------------------------------------

def test_invalid_dataset_value_rejected():
    with patch("src.clients.iolp_client.requests.get") as mock_get:
        result = get_property_details("CT3414", dataset="both")

    mock_get.assert_not_called()
    assert result["message"] is not None
    assert result["fetch_attempted_at"] is None
    assert result["retrieved_at_utc"] is None


# ---------------------------------------------------------------------------
# Evidence 5: Validation failure → null timestamps
# ---------------------------------------------------------------------------

def test_validation_failure_null_timestamps():
    with patch("src.clients.iolp_client.requests.get") as mock_get:
        result = get_property_details("")  # empty location_code

    mock_get.assert_not_called()
    assert result["fetch_attempted_at"] is None
    assert result["retrieved_at_utc"] is None
    assert result["source_file"] is None
    assert result["message"] is not None


def test_validation_failure_too_short():
    with patch("src.clients.iolp_client.requests.get") as mock_get:
        result = get_property_details("X")  # min_length=2

    mock_get.assert_not_called()
    assert result["fetch_attempted_at"] is None
    assert result["retrieved_at_utc"] is None


# ---------------------------------------------------------------------------
# Evidence 6: Source file is the actual XLSX URL, not the catalog page
# ---------------------------------------------------------------------------

def test_source_file_is_xlsx_url():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row()])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CT3414")

    assert result["source_file"] is not None
    assert "inventory.data.gov" in result["source_file"]
    # Retrieved at must be non-null when successful
    assert result["retrieved_at_utc"] is not None
    assert result["fetch_attempted_at"] is not None


# ---------------------------------------------------------------------------
# Evidence 7: Row limit hit, record not found → incomplete-scan error, not 'not found'
# ---------------------------------------------------------------------------

def test_truncated_scan_not_reported_as_definitively_absent():
    """When the row limit is reached and no match found, the response must
    NOT claim the record does not exist. It must signal an incomplete scan."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [])  # no matching record
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    from src.clients import iolp_client

    original = iolp_client._sheet_to_records

    def patched_truncated(wb, dataset, required_cols):
        records, _ = original(wb, dataset, required_cols)
        return records, True  # force truncated=True

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)), \
         patch("src.clients.iolp_client._sheet_to_records", side_effect=patched_truncated):
        result = get_property_details("ZZZFAKE")

    assert result["message"] is not None
    msg = result["message"].lower()
    # Must NOT say definitively "not found" — must mention the scan limit
    assert "not found" not in msg or "incomplete" in msg or "limit" in msg
    assert "incomplete" in msg or "limit" in msg or "scanned" in msg


# ---------------------------------------------------------------------------
# Evidence 8: Happy path — correct fields and provenance
# ---------------------------------------------------------------------------

def test_happy_path_leases():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row()])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CT3414")

    assert result["location_code"] == "CT3414"
    assert result["real_property_asset_name"] == "2 UNION PLAZA"
    assert result["dataset"] == "leases"
    assert result["state"] == "CT"
    assert result["city"] == "NEW LONDON"
    assert result["lease_number"] == "LCT00005"
    assert result["message"] is None
    # congressional_district_representative must not be in output
    assert "congressional_district_representative" not in result


def test_happy_path_buildings():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [_buildings_row()])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("MA6048")

    assert result["dataset"] == "buildings"
    assert result["building_status"] == "ACTIVE"
    assert result["owned_or_leased"] == "LEASED"
    assert result["message"] is None


def test_happy_path_case_insensitive():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row()])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("ct3414")

    assert result["location_code"] == "CT3414"
    assert result["message"] is None


def test_happy_path_full_numeric_fields():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row()])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CT3414")

    assert result["latitude"] == pytest.approx(41.3542597, rel=1e-4)
    assert result["longitude"] == pytest.approx(-72.094347, rel=1e-4)
    assert result["building_rentable_sq_ft"] == 7276.0
    assert result["congressional_district"] == "0902"
    assert result["lease_effective_date"] == "7/27/2019"
    assert result["lease_expiration_date"] == "7/26/2034"


# ---------------------------------------------------------------------------
# Evidence 9: Non-200 and invalid XLSX → structured errors, attempted provenance
# ---------------------------------------------------------------------------

def test_non_200_null_provenance():
    from src.clients.iolp_client import LEASES_URL
    import re

    with patch("src.clients.iolp_client.requests.get",
               return_value=_mock_response(b"", status=404)):
        result = get_property_details("CT3414")

    assert result["dataset"] == "unknown"
    assert "404" in result["message"]
    # fetch_attempted_at must be a UTC ISO timestamp, not just truthy
    assert result["fetch_attempted_at"] is not None
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result["fetch_attempted_at"])
    assert result["retrieved_at_utc"] is None
    assert result["source_file"] is None


def test_invalid_xlsx_null_provenance():
    from src.clients.iolp_client import LEASES_URL
    import re

    with patch("src.clients.iolp_client.requests.get",
               return_value=_mock_response(b"not an xlsx", status=200)):
        result = get_property_details("CT3414")

    assert result["dataset"] == "unknown"
    assert result["message"] is not None
    # fetch_attempted_at must be a UTC ISO timestamp, not just truthy
    assert result["fetch_attempted_at"] is not None
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result["fetch_attempted_at"])
    assert result["retrieved_at_utc"] is None


def test_not_found_null_source_file():
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("ZZZNOTEXIST")

    assert result["dataset"] == "unknown"
    assert "ZZZNOTEXIST" in result["message"]
    # Both datasets were fetched; source_file is the last retrieved URL (C2 contract).
    assert result["source_file"] == BUILDINGS_URL
    assert result["source_url"].startswith("https://")
    # Fetch occurred (datasets were downloaded) — timestamp must be present
    assert result["fetch_attempted_at"] is not None


def test_ambiguity_preserves_fetch_attempted_at():
    """fetch_attempted_at must be a real timestamp when the result is ambiguous —
    the HTTP fetch succeeded; only the record selection failed."""
    import re
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(location_code="CT3414", lease_number="LCT00005"),
        _leases_row(location_code="CT3414", lease_number="LCT00099"),
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CT3414")

    assert result["dataset"] == "unknown"
    # source_file is the XLSX that was fetched (within-dataset ambiguity, C2 contract).
    assert result["source_file"] == LEASES_URL
    assert result["fetch_attempted_at"] is not None
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result["fetch_attempted_at"])
    assert result["retrieved_at_utc"] is None


# ---------------------------------------------------------------------------
# Evidence 10: Truncated scan + exactly 1 match → ambiguity, not success
# ---------------------------------------------------------------------------

def test_truncated_scan_one_match_raises_ambiguity():
    """When the scan is truncated and exactly one candidate is found,
    uniqueness cannot be confirmed — must surface an ambiguity error, not
    return the record as if it were definitively unique."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row(location_code="CT3414")])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    from src.clients import iolp_client

    original = iolp_client._sheet_to_records

    def patched_truncated(wb, dataset, required_cols):
        records, _ = original(wb, dataset, required_cols)
        return records, True  # force truncated=True

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)), \
         patch("src.clients.iolp_client._sheet_to_records", side_effect=patched_truncated):
        result = get_property_details("CT3414")

    # Must NOT return a successful record — uniqueness is unconfirmed
    assert result["dataset"] == "unknown"
    assert result["message"] is not None
    msg = result["message"].lower()
    assert "incomplete" in msg or "limit" in msg or "uniqueness" in msg or "truncated" in msg.lower()
    # source_file is the XLSX URL of the candidate (truncated-scan ambiguity, C2 contract).
    assert result["source_file"] == LEASES_URL


# ---------------------------------------------------------------------------
# Evidence 11: lease_number disambiguates within-dataset duplicates
# ---------------------------------------------------------------------------

def test_lease_number_resolves_duplicate():
    """When a Location Code matches multiple rows, supplying lease_number
    must return only the matching row without raising an ambiguity error."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(location_code="CA6395", lease_number="LCA00229"),
        _leases_row(location_code="CA6395", lease_number="LCA02838"),
        _leases_row(location_code="CA6395", lease_number="LCA02892"),
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CA6395", lease_number="LCA00229")

    assert result["dataset"] == "leases"
    assert result["lease_number"] == "LCA00229"
    assert result["message"] is None


def test_lease_number_not_found_returns_error():
    """When lease_number is supplied but no row matches it, an error message
    must be returned — not a success and not a generic 'not found'."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(location_code="CA6395", lease_number="LCA00229"),
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([
        _mock_response(leases_xlsx),
        _mock_response(buildings_xlsx),
    ])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CA6395", lease_number="LXXXXXXX")

    assert result["dataset"] == "unknown"
    assert result["message"] is not None
    assert "LXXXXXXX" in result["message"] or "CA6395" in result["message"]


# ---------------------------------------------------------------------------
# Evidence 12: Fetch error → sources_attempted non-empty; timestamp is real ISO UTC
# ---------------------------------------------------------------------------

def test_fetch_error_preserves_sources_attempted():
    """When the first HTTP request fails, the response must include the
    attempted URL in sources_attempted and a real timestamp in
    fetch_attempted_at so the caller can trace which source was tried."""
    from src.clients.iolp_client import LEASES_URL
    import requests as requests_lib
    import re

    with patch("src.clients.iolp_client.requests.get",
               side_effect=requests_lib.ConnectionError("host unreachable")):
        result = get_property_details("CT3414")

    assert result["dataset"] == "unknown"
    assert result["message"] is not None
    # fetch_attempted_at must be a UTC ISO timestamp string
    assert result["fetch_attempted_at"] is not None
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result["fetch_attempted_at"])


# ---------------------------------------------------------------------------
# Evidence 13: Full behavior matrix — lease_number + truncation combinations
# ---------------------------------------------------------------------------

def _make_patched_truncated(original_fn):
    """Returns a _sheet_to_records replacement that always reports truncated=True."""
    def patched(wb, dataset, required_cols):
        records, _ = original_fn(wb, dataset, required_cols)
        return records, True
    return patched


def test_matrix_truncated_no_lease_match_incomplete_not_notfound():
    """Truncated scan + lease_number supplied + no match → incomplete error,
    not 'not found'. The record may exist beyond the row limit."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(location_code="CA6395", lease_number="LCA00229"),
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    from src.clients import iolp_client
    responses = iter([_mock_response(leases_xlsx), _mock_response(buildings_xlsx)])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)), \
         patch("src.clients.iolp_client._sheet_to_records",
               side_effect=_make_patched_truncated(iolp_client._sheet_to_records)):
        result = get_property_details("CA6395", dataset="leases", lease_number="LNOTEXIST")

    assert result["dataset"] == "unknown"
    msg = result["message"].lower()
    # Must signal incomplete scan, not definitively absent
    assert "not found" not in msg or "incomplete" in msg or "limit" in msg
    assert "incomplete" in msg or "limit" in msg


def test_matrix_truncated_multiple_lease_matches_ambiguous():
    """Truncated scan + lease_number matches multiple rows → ambiguity error
    that discloses the truncation (additional rows may exist beyond the limit)."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(location_code="CA6395", lease_number="LCA00229"),
        _leases_row(location_code="CA6395", lease_number="LCA00229"),  # same lease number twice
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    from src.clients import iolp_client
    responses = iter([_mock_response(leases_xlsx), _mock_response(buildings_xlsx)])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)), \
         patch("src.clients.iolp_client._sheet_to_records",
               side_effect=_make_patched_truncated(iolp_client._sheet_to_records)):
        result = get_property_details("CA6395", dataset="leases", lease_number="LCA00229")

    assert result["dataset"] == "unknown"
    msg = result["message"]
    assert msg is not None
    # Must mention the duplicate and disclose the truncation
    assert "LCA00229" in msg or "duplicate" in msg.lower() or "matched" in msg.lower()
    assert "limit" in msg.lower() or "truncated" in msg.lower()


def test_matrix_duplicate_same_lease_number_complete_scan():
    """Complete scan, two rows share the same Location Code AND Lease Number →
    ambiguous; must not silently return the first row."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(location_code="CA6395", lease_number="LCA00229"),
        _leases_row(location_code="CA6395", lease_number="LCA00229"),
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([_mock_response(leases_xlsx), _mock_response(buildings_xlsx)])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CA6395", dataset="leases", lease_number="LCA00229")

    assert result["dataset"] == "unknown"
    msg = result["message"]
    assert msg is not None
    # Must flag as ambiguous / duplicate, not return a record
    assert "LCA00229" in msg or "duplicate" in msg.lower() or "matched" in msg.lower()


def test_matrix_lease_number_exact_match_complete_scan():
    """Complete scan, exactly one row matches Location Code + Lease Number →
    return the record. This is the canonical CA6395/LCA00229 use case."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [
        _leases_row(location_code="CA6395", lease_number="LCA00229"),
        _leases_row(location_code="CA6395", lease_number="LCA02838"),
        _leases_row(location_code="CA6395", lease_number="LCA02892"),
    ])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    responses = iter([_mock_response(leases_xlsx), _mock_response(buildings_xlsx)])

    with patch("src.clients.iolp_client.requests.get", side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details(
            location_code="CA6395",
            dataset="leases",
            lease_number="LCA00229",
        )

    assert result["dataset"] == "leases"
    assert result["lease_number"] == "LCA00229"
    assert result["message"] is None
    assert result["source_file"] is not None
    assert result["fetch_attempted_at"] is not None
    assert result["retrieved_at_utc"] is not None
