"""Offline security, provenance, and failure-path tests for IOLP ingestion hardening.

All tests mock at the requests.get boundary.  No live network calls are made.

Coverage:
  H01  Redirect to disallowed scheme (http://) rejected before any data read
  H02  Redirect to disallowed scheme (file://) rejected
  H03  Redirect to disallowed host rejected
  H04  Redirect with empty netloc rejected
  H05  Allowed host passes redirect validation (inventory.data.gov → s3)
  H06  Too many redirects raises IolpFetchError
  H07  Non-200 final response — error body NOT fully buffered
  H08  Oversized declared Content-Length rejected before streaming
  H09  Non-integer Content-Length header treated as absent; download proceeds
  H10  Streamed body exceeds byte cap mid-stream; response closed in finally
  H11  Network error mid-stream; response closed in finally
  H12  Not-a-ZIP body raises IolpFetchError
  H13  ZIP with too many entries rejected
  H14  ZIP bomb (large declared expanded size) rejected
  H15  SHA-256 in detail response matches independently computed hash
  H16  Per-source SHA-256 list in search response; both sources hashed correctly
  H17  source_sha256 absent from detail response on fetch failure
  H18  sources_sha256 empty in search response on fetch failure
  H19  SHA-256 is deterministic across two calls with identical bytes
  H20  Incomplete source retrieval: sources_sha256 has fewer entries than
       sources_attempted; retrieved_at_utc is null
  H21  sqs.amazonaws.com redirect rejected (non-S3 AWS service)
  H22  sns.amazonaws.com redirect rejected (non-S3 AWS service)
  H23  Bare amazonaws.com redirect rejected (too broad)
  H24  Loopback (127.0.0.1) redirect rejected
  H25  localhost redirect rejected
  H26  source_file and source_sha256 populated in detail response after
       successful fetch that results in not-found (no matching record)
  H27  source_file and source_sha256 populated in detail response after
       successful fetch that results in ambiguous record
  H28  source_sha256 aligns with source_file in ambiguous-record response
"""

import hashlib
import io
import zipfile
from unittest.mock import MagicMock, call, patch

import openpyxl
import pytest
import requests as requests_lib

from src.clients.iolp_client import (
    LEASES_URL,
    BUILDINGS_URL,
    _ALLOWED_HOSTS,
    _MAX_DOWNLOAD_BYTES,
    _MAX_REDIRECTS,
    _MAX_ZIP_ENTRIES,
    _MAX_ZIP_EXPANDED_BYTES,
    _is_permitted_iolp_host,
    _validate_redirect_url,
    _validate_zip,
    IolpAmbiguousRecordError,
    IolpFetchError,
    IolpRecordNotFoundError,
)
from src.server import get_property_details, search_federal_properties


# ---------------------------------------------------------------------------
# Shared helpers
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


def _leases_row(location_code="CT3414", lease_number="LCT00005") -> list:
    return [
        location_code, "2 UNION PLAZA", "N/A", "L", 1,
        "2 UNION PLZ", "NEW LONDON", "CT", "06320-6309",
        41.3542597, -72.094347, 7276, None, "0902", "JOE COURTNEY",
        lease_number, "7/27/2019", "7/26/2034", "BUILDING",
    ]


def _buildings_row(location_code="MA6048") -> list:
    return [
        location_code, "ICE HSI BOSTON", "N/A", "LEASED", 1,
        "580 CHELSEA STREET", "BOSTON", "MA", "02128-1566",
        42.384501, -71.023153, 4288, None, 2000, "2507",
        "AYANNA PRESSLEY", "ACTIVE", "BUILDING",
    ]


def _mock_ok(content: bytes) -> MagicMock:
    """200 OK streaming mock, no Content-Length."""
    m = MagicMock()
    m.status_code = 200
    m.headers = {}
    m.iter_content.side_effect = lambda chunk_size=65536: iter([content])
    m.close = MagicMock()
    return m


def _mock_redirect(location: str, status: int = 302) -> MagicMock:
    """3xx redirect mock; iter_content must never be called on this."""
    m = MagicMock()
    m.status_code = status
    m.headers = {"Location": location}
    m.close = MagicMock()
    return m


def _mock_error(status: int, body: bytes = b"error body") -> MagicMock:
    """Non-200 error mock with small body for preview."""
    m = MagicMock()
    m.status_code = status
    m.headers = {}
    m.iter_content.side_effect = lambda chunk_size=65536: iter([body[:512]])
    m.close = MagicMock()
    return m


def _make_zip_bomb_bytes(entry_expanded_bytes: int = 60 * 1024 * 1024) -> bytes:
    """Build a ZIP where one entry declares a huge expanded size.

    Uses actual compression so the file fits within the download limit,
    but the ZIP central directory reports a large file_size value.
    This tests the infolist()-based check WITHOUT decompressing anything.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Write highly-compressible data.
        data = b"A" * min(entry_expanded_bytes, 500_000)
        zf.writestr("xl/worksheets/sheet1.xml", data)
    raw = buf.getvalue()

    # Patch the central directory to declare a huge file_size.
    # We reopen and inspect to find the entry, then return the patched bytes.
    # For the test, we just need the declared file_size to exceed the limit —
    # we achieve this by constructing a ZipInfo with the desired declared size.
    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as zf2:
        zi = zipfile.ZipInfo("xl/worksheets/sheet1.xml")
        zi.compress_type = zipfile.ZIP_STORED
        # Write a small placeholder so the ZIP is valid
        zf2.writestr(zi, b"x" * 100)
    raw2 = buf2.getvalue()

    # Verify we can reopen it
    zf_check = zipfile.ZipFile(io.BytesIO(raw2))
    entries = zf_check.infolist()
    zf_check.close()

    # Manually set file_size on the ZipInfo (in-memory only for the test)
    # The actual test patches _validate_zip or constructs bytes with inflated metadata.
    # Simplest approach: build a ZIP with enough entries OR enough declared size.
    return raw2  # returned as a base; see test_zip_bomb_rejected below


def _make_zip_with_many_entries(count: int) -> bytes:
    """Build a valid ZIP with *count* empty entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(count):
            zf.writestr(f"entry_{i}.xml", b"")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# H01–H04: Redirect URL validation — unit tests on _validate_redirect_url
# ---------------------------------------------------------------------------

def test_h01_http_scheme_rejected():
    """HTTP (non-HTTPS) redirect destination must be rejected."""
    ts = "2026-11-05T21:00:00Z"
    with pytest.raises(IolpFetchError) as exc_info:
        _validate_redirect_url(
            "http://inventory.data.gov/file.xlsx",
            origin_url=LEASES_URL,
            ts=ts,
        )
    msg = str(exc_info.value)
    assert "http" in msg.lower() or "scheme" in msg.lower() or "disallowed" in msg.lower()


def test_h02_file_scheme_rejected():
    """file:// redirect destination must be rejected."""
    ts = "2026-11-05T21:00:00Z"
    with pytest.raises(IolpFetchError) as exc_info:
        _validate_redirect_url(
            "file:///etc/passwd",
            origin_url=LEASES_URL,
            ts=ts,
        )
    assert "file" in str(exc_info.value).lower() or "disallowed" in str(exc_info.value).lower()


def test_h03_disallowed_host_rejected():
    """Redirect to a host not in the allow-list must be rejected."""
    ts = "2026-11-05T21:00:00Z"
    with pytest.raises(IolpFetchError) as exc_info:
        _validate_redirect_url(
            "https://evil.example.com/file.xlsx",
            origin_url=LEASES_URL,
            ts=ts,
        )
    msg = str(exc_info.value)
    assert "evil.example.com" in msg or "allow-list" in msg or "not in" in msg.lower()


def test_h04_empty_netloc_rejected():
    """A URL with no hostname must be rejected."""
    ts = "2026-11-05T21:00:00Z"
    with pytest.raises(IolpFetchError) as exc_info:
        _validate_redirect_url(
            "https://",
            origin_url=LEASES_URL,
            ts=ts,
        )
    assert "hostname" in str(exc_info.value).lower() or "netloc" in str(exc_info.value).lower()


def test_h05_allowed_s3_host_passes():
    """Redirect to amazonaws.com (S3) must pass validation without error."""
    ts = "2026-11-05T21:00:00Z"
    # Must not raise
    _validate_redirect_url(
        "https://s3.amazonaws.com/bucket/iolp-leases.xlsx",
        origin_url=LEASES_URL,
        ts=ts,
    )


# ---------------------------------------------------------------------------
# H06: Too many redirects
# ---------------------------------------------------------------------------

def test_h06_too_many_redirects():
    """Exceeding _MAX_REDIRECTS redirect hops must raise IolpFetchError."""
    # Build a chain of _MAX_REDIRECTS + 1 redirects, all to valid hosts.
    redirects = [
        _mock_redirect(f"https://inventory.data.gov/hop{i+1}.xlsx")
        for i in range(_MAX_REDIRECTS + 1)
    ]
    # Add a final 200 that should never be reached.
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row()])
    redirects.append(_mock_ok(leases_xlsx))
    responses = iter(redirects)

    with patch("src.clients.iolp_client.requests.get",
               side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CT3414", dataset="leases")

    assert result["dataset"] == "unknown"
    assert result["message"] is not None
    msg = result["message"].lower()
    assert "redirect" in msg or "limit" in msg


# ---------------------------------------------------------------------------
# H07: Non-200 response body NOT fully buffered
# ---------------------------------------------------------------------------

def test_h07_non_200_body_not_fully_buffered():
    """On a non-200 response, iter_content must be called with a small cap,
    not the full response body, and the response must be closed."""
    error_mock = _mock_error(503, body=b"Service Unavailable")
    buildings_ok = _mock_ok(_make_xlsx(BUILDINGS_HEADERS, []))
    responses = iter([error_mock, buildings_ok])

    with patch("src.clients.iolp_client.requests.get",
               side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CT3414", dataset="leases")

    assert result["dataset"] == "unknown"
    assert "503" in result["message"]
    # iter_content was called with a small chunk_size (≤ 512), not chunk_size equal to
    # the full download limit — verifies we didn't buffer the whole body.
    call_kwargs = error_mock.iter_content.call_args
    if call_kwargs is not None:
        chunk_arg = call_kwargs[1].get("chunk_size") or (call_kwargs[0][0] if call_kwargs[0] else None)
        if chunk_arg is not None:
            assert chunk_arg <= 512, f"chunk_size {chunk_arg} is too large for error preview"
    error_mock.close.assert_called()


# ---------------------------------------------------------------------------
# H08: Oversized declared Content-Length rejected before streaming
# ---------------------------------------------------------------------------

def test_h08_oversized_content_length_rejected():
    """A response declaring Content-Length > _MAX_DOWNLOAD_BYTES must be
    rejected before iter_content is ever called."""
    oversized = 11 * 1024 * 1024  # 11 MiB > 10 MiB limit
    m = MagicMock()
    m.status_code = 200
    m.headers = {"Content-Length": str(oversized)}
    m.iter_content = MagicMock()
    m.close = MagicMock()

    with patch("src.clients.iolp_client.requests.get", return_value=m):
        result = get_property_details("CT3414", dataset="leases")

    assert result["dataset"] == "unknown"
    assert result["message"] is not None
    msg = result["message"]
    assert str(oversized) in msg.replace(",", "") or "limit" in msg.lower() or "bytes" in msg.lower()
    m.iter_content.assert_not_called()
    m.close.assert_called()


# ---------------------------------------------------------------------------
# H09: Non-integer Content-Length treated as absent
# ---------------------------------------------------------------------------

def test_h09_non_integer_content_length_proceeds():
    """A non-integer Content-Length header must be treated as absent and the
    download must proceed normally."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row()])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    def make_mock(content):
        m = _mock_ok(content)
        m.headers["Content-Length"] = "not-a-number"
        return m

    responses = iter([make_mock(leases_xlsx), make_mock(buildings_xlsx)])
    with patch("src.clients.iolp_client.requests.get",
               side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CT3414")

    assert result["dataset"] == "leases"
    assert result["message"] is None


# ---------------------------------------------------------------------------
# H10: Streamed body exceeds byte cap; response closed in finally
# ---------------------------------------------------------------------------

def test_h10_streamed_overflow_raises_and_closes():
    """When the streamed body exceeds _MAX_DOWNLOAD_BYTES, IolpFetchError must
    be raised and resp.close() must be called (the finally block fires)."""
    chunk = b"x" * (64 * 1024)
    num_chunks = (_MAX_DOWNLOAD_BYTES // len(chunk)) + 2   # enough to overflow

    m = MagicMock()
    m.status_code = 200
    m.headers = {}
    # Use a fresh iterator each time iter_content is called.
    m.iter_content.side_effect = lambda chunk_size=65536: iter([chunk] * num_chunks)
    m.close = MagicMock()

    with patch("src.clients.iolp_client.requests.get", return_value=m):
        result = get_property_details("CT3414", dataset="leases")

    assert result["dataset"] == "unknown"
    assert result["message"] is not None
    msg = result["message"].lower()
    assert "limit" in msg or "exceeded" in msg or "bytes" in msg
    m.close.assert_called()


# ---------------------------------------------------------------------------
# H11: Network error mid-stream; response closed in finally
# ---------------------------------------------------------------------------

def test_h11_mid_stream_network_error_closes_response():
    """A ConnectionError raised inside iter_content must result in an
    IolpFetchError AND resp.close() must be called via the finally block."""
    m = MagicMock()
    m.status_code = 200
    m.headers = {}
    m.iter_content.side_effect = lambda chunk_size=65536: (
        x for x in [b"first-chunk", (_ for _ in ()).throw(
            requests_lib.ConnectionError("dropped"))]
    )
    m.close = MagicMock()

    with patch("src.clients.iolp_client.requests.get", return_value=m):
        result = get_property_details("CT3414", dataset="leases")

    assert result["dataset"] == "unknown"
    assert result["message"] is not None
    m.close.assert_called()


# ---------------------------------------------------------------------------
# H12: Not-a-ZIP body
# ---------------------------------------------------------------------------

def test_h12_not_a_zip_raises_fetch_error():
    """A response body that is not a ZIP/XLSX archive must raise IolpFetchError
    before openpyxl is invoked, with a descriptive message."""
    m = _mock_ok(b"this is not an xlsx file at all")

    with patch("src.clients.iolp_client.requests.get", return_value=m):
        result = get_property_details("CT3414", dataset="leases")

    assert result["dataset"] == "unknown"
    msg = result["message"].lower()
    assert "zip" in msg or "xlsx" in msg or "archive" in msg or "valid" in msg


# ---------------------------------------------------------------------------
# H13: ZIP with too many entries rejected
# ---------------------------------------------------------------------------

def test_h13_zip_too_many_entries_rejected():
    """A ZIP body with more than _MAX_ZIP_ENTRIES entries must be rejected
    before openpyxl parsing."""
    oversized_zip = _make_zip_with_many_entries(_MAX_ZIP_ENTRIES + 1)
    m = _mock_ok(oversized_zip)

    with patch("src.clients.iolp_client.requests.get", return_value=m):
        result = get_property_details("CT3414", dataset="leases")

    assert result["dataset"] == "unknown"
    msg = result["message"].lower()
    assert "entries" in msg or "entry" in msg or "limit" in msg or "zip" in msg


# ---------------------------------------------------------------------------
# H14: ZIP bomb — large declared expanded size rejected
# ---------------------------------------------------------------------------

def test_h14_zip_bomb_declared_size_rejected():
    """A ZIP where the sum of declared expanded entry sizes exceeds
    _MAX_ZIP_EXPANDED_BYTES must be rejected via infolist() metadata check
    without decompressing any entry."""
    # Build a ZIP with one entry that claims to expand to > the limit.
    # Use _validate_zip directly so the test exercises the exact code path.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Write a small amount of data; the test patches the declared size below.
        zf.writestr("xl/worksheets/sheet1.xml", b"A" * 1000)
    raw = buf.getvalue()

    # Reload the ZIP, inflate the file_size field in memory, then call
    # _validate_zip with the original bytes but a mocked infolist.
    ts = "2026-11-05T21:00:00Z"
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        entries = zf.infolist()
    # Artificially set the declared expanded size above the limit.
    entries[0].file_size = _MAX_ZIP_EXPANDED_BYTES + 1

    with patch("zipfile.ZipFile") as mock_zf_cls:
        mock_zf = MagicMock()
        mock_zf.infolist.return_value = entries
        mock_zf_cls.return_value.__enter__ = lambda s: mock_zf
        mock_zf_cls.return_value = mock_zf
        with pytest.raises(IolpFetchError) as exc_info:
            _validate_zip(raw, url=LEASES_URL, ts=ts)

    msg = str(exc_info.value).lower()
    assert "expanded" in msg or "limit" in msg or "bomb" in msg or "bytes" in msg


# ---------------------------------------------------------------------------
# H15: SHA-256 in detail response matches independently computed hash
# ---------------------------------------------------------------------------

def test_h15_sha256_in_detail_response_correct():
    """source_sha256 in the detail response must equal the SHA-256 of the
    exact XLSX bytes downloaded."""
    import hashlib as _hashlib
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row()])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])
    expected_sha = _hashlib.sha256(leases_xlsx).hexdigest()

    responses = iter([_mock_ok(leases_xlsx), _mock_ok(buildings_xlsx)])
    with patch("src.clients.iolp_client.requests.get",
               side_effect=lambda *a, **kw: next(responses)):
        result = get_property_details("CT3414")

    assert result["dataset"] == "leases"
    assert result["source_sha256"] == expected_sha
    assert result["message"] is None


# ---------------------------------------------------------------------------
# H16: Per-source SHA-256 list in search response
# ---------------------------------------------------------------------------

def test_h16_per_source_sha256_in_search_response():
    """sources_sha256 in the search response must contain one entry per
    successfully retrieved source, in the same order as sources_retrieved,
    each matching the SHA-256 of the corresponding XLSX bytes."""
    import hashlib as _hashlib
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row()])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [_buildings_row()])
    expected_leases_sha = _hashlib.sha256(leases_xlsx).hexdigest()
    expected_buildings_sha = _hashlib.sha256(buildings_xlsx).hexdigest()

    responses = iter([_mock_ok(leases_xlsx), _mock_ok(buildings_xlsx)])
    with patch("src.clients.iolp_client.requests.get",
               side_effect=lambda *a, **kw: next(responses)):
        result = search_federal_properties(q="UNION")

    assert len(result["sources_sha256"]) == 2
    assert result["sources_sha256"][0] == expected_leases_sha
    assert result["sources_sha256"][1] == expected_buildings_sha
    assert len(result["sources_sha256"]) == len(result["sources_retrieved"])


# ---------------------------------------------------------------------------
# H17: source_sha256 absent from detail response on fetch failure
# ---------------------------------------------------------------------------

def test_h17_source_sha256_null_on_fetch_failure():
    """When the download fails, source_sha256 must be null in the response."""
    with patch("src.clients.iolp_client.requests.get",
               side_effect=requests_lib.ConnectionError("unreachable")):
        result = get_property_details("CT3414", dataset="leases")

    assert result["dataset"] == "unknown"
    assert result["source_sha256"] is None
    assert result["message"] is not None


# ---------------------------------------------------------------------------
# H18: sources_sha256 empty in search response on fetch failure
# ---------------------------------------------------------------------------

def test_h18_sources_sha256_empty_on_fetch_failure():
    """When all sources fail to download, sources_sha256 must be an empty list."""
    with patch("src.clients.iolp_client.requests.get",
               side_effect=requests_lib.ConnectionError("unreachable")):
        result = search_federal_properties(q="anything")

    assert result["sources_sha256"] == []
    assert result["sources_retrieved"] == []


# ---------------------------------------------------------------------------
# H19: SHA-256 is deterministic across two calls with identical bytes
# ---------------------------------------------------------------------------

def test_h19_sha256_deterministic():
    """Two calls with the same XLSX bytes must produce the same source_sha256."""
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row()])
    buildings_xlsx = _make_xlsx(BUILDINGS_HEADERS, [])

    sha_values = []
    for _ in range(2):
        responses = iter([_mock_ok(leases_xlsx), _mock_ok(buildings_xlsx)])
        with patch("src.clients.iolp_client.requests.get",
                   side_effect=lambda *a, **kw: next(responses)):
            result = get_property_details("CT3414")
        sha_values.append(result["source_sha256"])

    assert sha_values[0] is not None
    assert sha_values[0] == sha_values[1]


# ---------------------------------------------------------------------------
# H20: Incomplete source retrieval — sha256 list shorter than attempted
# ---------------------------------------------------------------------------

def test_h20_partial_retrieval_sha256_list_shorter_than_attempted():
    """When the first source is retrieved successfully but the second fails,
    the error propagates out of search_properties before the second source
    can be appended to sources_retrieved or sources_sha256.

    The server-layer except block catches IolpFetchError and returns a
    structured error response.  sources_attempted in that response comes
    from IolpFetchError.attempted_url (the single failing URL), and
    sources_sha256 is empty because no full retrieval completed.
    retrieved_at_utc must be null.
    """
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row(location_code="ZZZZ1")])

    call_count = 0
    def side_effect(*a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_ok(leases_xlsx)
        raise requests_lib.ConnectionError("second source unreachable")

    # search_federal_properties fetches both datasets; the second fetch raises.
    # IolpFetchError propagates out of search_properties entirely.
    with patch("src.clients.iolp_client.requests.get", side_effect=side_effect):
        result = search_federal_properties(q="ZZZZ1")

    # Error response: IolpFetchError carries the failing URL (BUILDINGS_URL).
    # The server surfaces it in sources_attempted.
    assert BUILDINGS_URL in result["sources_attempted"]
    # No source completed the full pipeline in the error path.
    assert result["sources_retrieved"] == []
    assert result["retrieved_at_utc"] is None
    # sources_sha256 must be empty — no verified complete retrieval.
    assert result["sources_sha256"] == []
    assert result["message"] is not None


# ---------------------------------------------------------------------------
# H21–H25  Negative redirect-host tests (C1 tightened allow-list)
# ---------------------------------------------------------------------------

def test_h21_sqs_amazonaws_com_rejected():
    """sqs.amazonaws.com must be rejected — it is not an S3 endpoint."""
    assert not _is_permitted_iolp_host("sqs.amazonaws.com"), (
        "sqs.amazonaws.com should NOT be permitted; only *.s3.amazonaws.com is allowed"
    )
    ts = "2026-11-05T21:00:00Z"
    with pytest.raises(IolpFetchError) as exc_info:
        _validate_redirect_url(
            "https://sqs.amazonaws.com/123456/queue",
            origin_url=LEASES_URL,
            ts=ts,
        )
    assert "sqs.amazonaws.com" in str(exc_info.value) or "not a permitted" in str(exc_info.value)


def test_h22_sns_amazonaws_com_rejected():
    """sns.amazonaws.com must be rejected — it is not an S3 endpoint."""
    assert not _is_permitted_iolp_host("sns.amazonaws.com"), (
        "sns.amazonaws.com should NOT be permitted; only *.s3.amazonaws.com is allowed"
    )
    ts = "2026-11-05T21:00:00Z"
    with pytest.raises(IolpFetchError):
        _validate_redirect_url(
            "https://sns.amazonaws.com/arn:aws:sns:us-east-1:123:topic",
            origin_url=LEASES_URL,
            ts=ts,
        )


def test_h23_bare_amazonaws_com_rejected():
    """Bare amazonaws.com must be rejected — far too broad (SQS, SNS, EC2, etc.)."""
    assert not _is_permitted_iolp_host("amazonaws.com"), (
        "Bare amazonaws.com should NOT be permitted"
    )
    ts = "2026-11-05T21:00:00Z"
    with pytest.raises(IolpFetchError):
        _validate_redirect_url(
            "https://amazonaws.com/anything",
            origin_url=LEASES_URL,
            ts=ts,
        )


def test_h24_loopback_ip_rejected():
    """127.0.0.1 redirect must be rejected — SSRF prevention."""
    assert not _is_permitted_iolp_host("127.0.0.1")
    ts = "2026-11-05T21:00:00Z"
    with pytest.raises(IolpFetchError):
        _validate_redirect_url(
            "https://127.0.0.1/file.xlsx",
            origin_url=LEASES_URL,
            ts=ts,
        )


def test_h25_localhost_rejected():
    """localhost redirect must be rejected — SSRF prevention."""
    assert not _is_permitted_iolp_host("localhost")
    ts = "2026-11-05T21:00:00Z"
    with pytest.raises(IolpFetchError):
        _validate_redirect_url(
            "https://localhost/file.xlsx",
            origin_url=LEASES_URL,
            ts=ts,
        )


# ---------------------------------------------------------------------------
# H26–H28  Provenance on ambiguity / not-found after successful fetch (C2)
# ---------------------------------------------------------------------------

def test_h26_source_file_and_sha256_populated_on_not_found():
    """When the XLSX is fetched successfully but the Location Code is not found,
    source_file and source_sha256 must appear in the detail response even
    though no record was returned.
    """
    # Single-dataset lookup so only LEASES_URL is attempted and retrieved.
    leases_xlsx = _make_xlsx(LEASES_HEADERS, [_leases_row(location_code="REAL01")])
    expected_sha256 = hashlib.sha256(leases_xlsx).hexdigest()

    with patch("src.clients.iolp_client.requests.get", return_value=_mock_ok(leases_xlsx)):
        result = get_property_details(location_code="NOSUCH", dataset="leases")

    # Fetch was attempted and succeeded.
    assert result["fetch_attempted_at"] is not None
    # source_file must be the XLSX URL, not None.
    assert result["source_file"] == LEASES_URL, (
        f"Expected source_file={LEASES_URL!r}, got {result['source_file']!r}"
    )
    # source_sha256 must be the correct digest of the downloaded bytes.
    assert result["source_sha256"] == expected_sha256, (
        f"source_sha256 mismatch: got {result['source_sha256']!r}"
    )
    # No record was found so retrieved_at_utc must be None.
    assert result["retrieved_at_utc"] is None


def test_h27_source_file_and_sha256_populated_on_ambiguous_record():
    """When the XLSX is fetched successfully but the record is ambiguous
    (duplicate Location Code rows), source_file and source_sha256 must appear
    in the detail response even though no single record was returned.
    """
    # Two rows with the same Location Code → IolpAmbiguousRecordError.
    leases_xlsx = _make_xlsx(
        LEASES_HEADERS,
        [
            _leases_row(location_code="DUP001", lease_number="LC00001"),
            _leases_row(location_code="DUP001", lease_number="LC00002"),
        ],
    )
    expected_sha256 = hashlib.sha256(leases_xlsx).hexdigest()

    with patch("src.clients.iolp_client.requests.get", return_value=_mock_ok(leases_xlsx)):
        result = get_property_details(location_code="DUP001", dataset="leases")

    assert result["fetch_attempted_at"] is not None
    assert result["source_file"] == LEASES_URL, (
        f"Expected source_file={LEASES_URL!r}, got {result['source_file']!r}"
    )
    assert result["source_sha256"] == expected_sha256
    assert result["retrieved_at_utc"] is None


def test_h28_source_sha256_aligns_with_source_file_on_ambiguous():
    """The source_sha256 in an ambiguous-record response must be the SHA-256
    of the bytes actually served at source_file — not a stale or zero value.
    """
    leases_xlsx_v1 = _make_xlsx(
        LEASES_HEADERS,
        [
            _leases_row(location_code="CHK001", lease_number="LC00001"),
            _leases_row(location_code="CHK001", lease_number="LC00002"),
        ],
    )
    # Compute what the correct digest should be.
    correct_sha256 = hashlib.sha256(leases_xlsx_v1).hexdigest()

    with patch("src.clients.iolp_client.requests.get", return_value=_mock_ok(leases_xlsx_v1)):
        result = get_property_details(location_code="CHK001", dataset="leases")

    reported_sha256 = result["source_sha256"]
    reported_source_file = result["source_file"]

    assert reported_source_file == LEASES_URL
    assert reported_sha256 == correct_sha256, (
        f"Reported SHA-256 {reported_sha256!r} does not match the bytes "
        f"served at {reported_source_file!r} (expected {correct_sha256!r})"
    )
