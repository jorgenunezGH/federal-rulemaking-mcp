"""Thin client for the IOLP (Inventory of Owned and Leased Properties) dataset.

Within-dataset duplicate handling
----------------------------------
A Location Code identifies a property, not a unique lease. Multiple lease rows
can share the same Location Code (e.g. a building with consecutive leases).
get_property() raises IolpAmbiguousRecordError when more than one row matches
within a single dataset. The caller should inspect the search results to
identify which record is intended and may need to use a different identifier
(e.g. Lease Number) for disambiguation.

Data source provenance
----------------------
Dataset:       Inventory of Owned and Leased Properties (IOLP)
Publisher:     U.S. General Services Administration (GSA)
Catalog page:  https://catalog.data.gov/dataset/inventory-of-owned-and-leased-properties-iolp
Delivery:      Two XLSX files hosted on inventory.data.gov (S3-backed).

  Leases file
    Resource ID:  13886719-6b8a-4596-8c82-5b32664005f7
    URL:          https://inventory.data.gov/dataset/9a9e946e-124e-46f5-a934-e458a6c1c2b2
                  /resource/13886719-6b8a-4596-8c82-5b32664005f7
                  /download/2026-10-30-iolp-leases.xlsx
    Observed (2026-11-05): 7,078 data rows, 19 columns
    Columns: Location Code, Real Property Asset Name, Installation Name,
             Federal Leased Code, GSA Region, Street Address, City, State,
             Zip Code, Latitude, Longitude, Building Rentable Square Feet,
             Available Square Feet, Congressional District,
             Congressional District Representative, Lease Number,
             Lease Effective Date, Lease Expiration Date,
             Real Property Asset type

  Buildings file
    Resource ID:  ad1a66f2-8019-43d9-b625-ea55e1f09677
    URL:          https://inventory.data.gov/dataset/9a9e946e-124e-46f5-a934-e458a6c1c2b2
                  /resource/ad1a66f2-8019-43d9-b625-ea55e1f09677
                  /download/2026-10-30-iolp-buildings.xlsx
    Observed (2026-11-05): 7,746 data rows, 18 columns
    Columns: Location Code, Real Property Asset Name, Installation Name,
             Owned or Leased, GSA Region, Street Address, City, State,
             Zip Code, Latitude, Longitude, Building Rentable Square Feet,
             Available Square Feet, Construction Date, Congressional District,
             Congressional District Representative Name, Building Status,
             Real Property Asset Type

Access:     No API key required; files are publicly accessible.
Freshness:  The file-name date (2026-10-30) is included in the URL but does
            not guarantee that timestamp reflects the dataset's actual update
            date. retrieved_at_utc in every response records when the file
            was fetched by this client.

Row cap:    _MAX_ROWS limits in-memory processing per XLSX to avoid
            unbounded resource use if the dataset grows or a malformed
            file is returned.
"""

import hashlib
import io
import zipfile
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

import openpyxl
import requests

IOLP_DATASET_URL = (
    "https://catalog.data.gov/dataset/inventory-of-owned-and-leased-properties-iolp"
)

_BASE = (
    "https://inventory.data.gov/dataset"
    "/9a9e946e-124e-46f5-a934-e458a6c1c2b2/resource"
)

LEASES_URL = (
    f"{_BASE}/13886719-6b8a-4596-8c82-5b32664005f7"
    "/download/2026-10-30-iolp-leases.xlsx"
)

BUILDINGS_URL = (
    f"{_BASE}/ad1a66f2-8019-43d9-b625-ea55e1f09677"
    "/download/2026-10-30-iolp-buildings.xlsx"
)

# Hard cap on rows processed per XLSX. The observed files have ~8 k rows;
# a cap of 50 k prevents runaway processing if an unexpectedly large file
# is returned, while leaving ample headroom for dataset growth.
_MAX_ROWS = 50_000

# Hard cap on cells (rows × columns) in the active worksheet.  Protects
# against sheets with extreme dimensions even if row count is below _MAX_ROWS.
_MAX_CELLS = 5_000_000

# Timeout for XLSX downloads in seconds. Files are ~1 MB; 30 s is generous.
_TIMEOUT = 30

# Maximum raw download bytes per XLSX (10 MiB).  Known files are ~1 MB;
# 10 MiB gives 10× headroom.  The limit is enforced twice: once against
# a declared Content-Length header (before streaming begins) and once
# against the actual cumulative bytes received during streaming.
_MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024       # 10 MiB

# Maximum total *declared* expanded bytes across all ZIP central-directory
# entries (50 MiB).  This catches ZIP-bomb-style archives before openpyxl
# decompresses any entry.  The check uses infolist() metadata only and
# does not decompress anything.
_MAX_ZIP_EXPANDED_BYTES = 50 * 1024 * 1024   # 50 MiB

# Maximum number of entries in the ZIP central directory.  A well-formed
# XLSX has O(10) parts; 200 is a generous ceiling.
_MAX_ZIP_ENTRIES = 200

# Streaming chunk size for download.
_CHUNK_SIZE = 64 * 1024                      # 64 KiB

# Maximum redirect hops before the client gives up.  requests follows
# redirects internally; we validate the *final* destination URL.
_MAX_REDIRECTS = 5

# Permitted URL schemes for every hop in the redirect chain.
_ALLOWED_SCHEMES = frozenset({"https"})

# Permitted hostnames for IOLP XLSX downloads.
#
# inventory.data.gov — primary IOLP download host (exact match only).
#
# s3.amazonaws.com — AWS S3 path-style endpoint used when inventory.data.gov
#   redirects to S3.  Permitted as an exact match OR as the suffix of a
#   virtual-hosted-style bucket URL (e.g. bucket-name.s3.amazonaws.com or
#   s3.region.amazonaws.com).
#
# s3-us-gov-west-1.amazonaws.com — AWS GovCloud (US-West) S3 regional endpoint.
#   inventory.data.gov is hosted on cloud.gov (a GSA FedRAMP platform backed by
#   AWS GovCloud).  Live HEAD checks confirm the download redirects to this exact
#   host; it is therefore permitted as an additional exact match.
#
# Deliberately excluded:
#   - Bare ``amazonaws.com`` — would permit SQS, SNS, EC2, and every other
#     AWS service at *.amazonaws.com, far beyond the S3 storage tier needed here.
#   - Wildcard ``*.amazonaws.com`` — same reason; too broad.
#   - ``data.gov`` — parent domain of inventory.data.gov; too broad.
#
# The host check in _validate_redirect_url enforces these rules explicitly
# rather than using a simple set-membership suffix test.
_ALLOWED_HOSTS = frozenset({
    "inventory.data.gov",
    "s3.amazonaws.com",
    "s3-us-gov-west-1.amazonaws.com",
})

# Required columns in each dataset. Missing any of these raises IolpClientError
# so callers never receive silently incomplete records.
_REQUIRED_LEASES_COLS = frozenset({
    "Location Code", "Real Property Asset Name", "Street Address",
    "City", "State", "Zip Code",
})
_REQUIRED_BUILDINGS_COLS = frozenset({
    "Location Code", "Real Property Asset Name", "Street Address",
    "City", "State", "Zip Code",
})


class IolpClientError(Exception):
    """Raised when the IOLP XLSX cannot be fetched or parsed."""


class IolpFetchError(IolpClientError):
    """Raised when an HTTP request was issued but failed (non-200, timeout,
    or invalid response body).

    Carries the attempted source URL and attempt timestamp so the caller can
    report which source was tried without implying retrieval succeeded.

    Attributes
    ----------
    message:           Human-readable reason.
    attempted_url:     The XLSX URL that was requested; may be None when the
                       error occurred before a URL was selected.
    fetch_attempted_at: UTC timestamp of the attempt; may be None.
    """

    def __init__(
        self,
        message: str,
        attempted_url: str | None = None,
        fetch_attempted_at: str | None = None,
    ) -> None:
        super().__init__(message)
        self.attempted_url = attempted_url
        self.fetch_attempted_at = fetch_attempted_at


class IolpRecordNotFoundError(IolpClientError):
    """Raised when a Location Code does not exist in the searched dataset(s).

    Only raised when the scan was complete. If processing stopped at the row
    limit, IolpClientError is raised instead with an explicit incomplete-scan
    message so the caller does not present a definitive 'not found' response.

    Attributes
    ----------
    fetch_attempted_at: UTC timestamp of when the HTTP attempt was made.
                        Allows the server to surface provenance even when
                        no record was found.
    source_file:        The XLSX URL that was successfully fetched before the
                        not-found determination, or None if no fetch completed.
    source_sha256:      SHA-256 hex digest of that download, or None.
    """

    def __init__(
        self,
        message: str,
        fetch_attempted_at: str | None = None,
        source_file: str | None = None,
        source_sha256: str | None = None,
    ) -> None:
        super().__init__(message)
        self.fetch_attempted_at = fetch_attempted_at
        self.source_file = source_file
        self.source_sha256 = source_sha256


class IolpAmbiguousRecordError(IolpClientError):
    """Raised when a Location Code matches more than one record.

    This covers:
    - Within-dataset duplicates: multiple rows share the same Location Code
      in a single XLSX (e.g. consecutive leases on one property).
    - Cross-dataset matches: same code appears in both leases and buildings.
    - Truncated-scan match: exactly one match was found but the scan was
      incomplete, so uniqueness cannot be established.

    Callers should supply an explicit ``dataset`` and/or ``lease_number``
    parameter to narrow the result, or present the candidate list to the user.

    Attributes
    ----------
    candidates: list of raw row dicts from the matching dataset(s).
    fetch_attempted_at: UTC timestamp of when the HTTP attempt was made.
                        Allows the server to surface provenance even when
                        the result is ambiguous.
    source_file:        The XLSX URL that was successfully fetched before the
                        ambiguity determination, or None if no fetch completed.
    source_sha256:      SHA-256 hex digest of that download, or None.
    """

    def __init__(
        self,
        message: str,
        candidates: list | None = None,
        fetch_attempted_at: str | None = None,
        source_file: str | None = None,
        source_sha256: str | None = None,
    ) -> None:
        super().__init__(message)
        self.candidates: list = candidates or []
        self.fetch_attempted_at = fetch_attempted_at
        self.source_file = source_file
        self.source_sha256 = source_sha256


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_permitted_iolp_host(netloc: str) -> bool:
    """Return True when *netloc* (lowercase, port stripped) is a permitted
    IOLP download host.

    Permitted patterns:
    - ``inventory.data.gov``              — exact match only.
    - ``s3.amazonaws.com``                — exact match (path-style S3).
    - ``*.s3.amazonaws.com``              — virtual-hosted or regional S3 bucket
                                            (e.g. bucket.s3.amazonaws.com).
    - ``s3-us-gov-west-1.amazonaws.com``  — AWS GovCloud (US-West) S3; exact match.
                                            inventory.data.gov redirects here in
                                            production (confirmed by live HEAD check
                                            2026-11-06).

    All other hostnames are rejected, including bare ``amazonaws.com``,
    non-S3 AWS services (sqs.amazonaws.com, sns.amazonaws.com, etc.), and
    arbitrary ``*.amazonaws.com`` subdomains.
    """
    if netloc == "inventory.data.gov":
        return True
    if netloc == "s3.amazonaws.com":
        return True
    if netloc.endswith(".s3.amazonaws.com"):
        return True
    if netloc == "s3-us-gov-west-1.amazonaws.com":
        return True
    return False


def _validate_redirect_url(url: str, origin_url: str, ts: str) -> None:
    """Verify that *url* is a permitted IOLP download destination.

    Permitted destinations (enforced by ``_is_permitted_iolp_host``):
    - Scheme must be ``https`` — no plain HTTP, file://, ftp://, etc.
    - Hostname must be ``inventory.data.gov`` (exact), ``s3.amazonaws.com``
      (exact), or a ``*.s3.amazonaws.com`` subdomain.
    - Non-S3 AWS services (SQS, SNS, EC2, arbitrary *.amazonaws.com) are
      explicitly rejected.

    Raises ``IolpFetchError`` (using *origin_url* as ``attempted_url``) when
    any condition is violated.  The network request is issued only after this
    function returns without raising.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise IolpFetchError(
            f"IOLP redirect destination uses a disallowed scheme "
            f"({parsed.scheme!r}): {url!r}. Only HTTPS is permitted.",
            attempted_url=origin_url,
            fetch_attempted_at=ts,
        )
    netloc = parsed.netloc.lower().split(":")[0]   # strip optional port
    if not netloc:
        raise IolpFetchError(
            f"IOLP redirect destination has no hostname: {url!r}.",
            attempted_url=origin_url,
            fetch_attempted_at=ts,
        )
    if not _is_permitted_iolp_host(netloc):
        raise IolpFetchError(
            f"IOLP redirect destination host {netloc!r} is not a permitted "
            f"IOLP download host (inventory.data.gov or *.s3.amazonaws.com). "
            f"Rejecting to prevent unexpected data exposure.",
            attempted_url=origin_url,
            fetch_attempted_at=ts,
        )


def _validate_zip(body: bytes, url: str, ts: str) -> None:
    """Inspect the ZIP central-directory metadata before openpyxl parsing.

    Checks (all performed on infolist() metadata — nothing is decompressed):
    - ``body`` is a valid ZIP archive.
    - Entry count ≤ ``_MAX_ZIP_ENTRIES``.
    - Sum of declared expanded sizes ≤ ``_MAX_ZIP_EXPANDED_BYTES``.

    Raises ``IolpFetchError`` on any violation.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile as exc:
        raise IolpFetchError(
            f"IOLP file from {url} is not a valid ZIP/XLSX archive: {exc}",
            attempted_url=url,
            fetch_attempted_at=ts,
        ) from exc

    try:
        entries = zf.infolist()
    finally:
        zf.close()

    if len(entries) > _MAX_ZIP_ENTRIES:
        raise IolpFetchError(
            f"IOLP file from {url} contains {len(entries):,} ZIP entries, "
            f"exceeding the {_MAX_ZIP_ENTRIES}-entry limit. "
            f"Expected an XLSX with O(10) parts.",
            attempted_url=url,
            fetch_attempted_at=ts,
        )

    total_expanded = sum(e.file_size for e in entries)
    if total_expanded > _MAX_ZIP_EXPANDED_BYTES:
        raise IolpFetchError(
            f"IOLP file from {url} declares {total_expanded:,} total "
            f"expanded bytes across ZIP entries, exceeding the "
            f"{_MAX_ZIP_EXPANDED_BYTES:,}-byte limit. "
            f"This may indicate a compressed-bomb structure.",
            attempted_url=url,
            fetch_attempted_at=ts,
        )


def _fetch_xlsx(
    url: str,
    fetch_attempted_at: str | None = None,
) -> tuple["openpyxl.Workbook", str]:
    """Download an XLSX from *url*, enforce size and redirect limits, and
    compute a SHA-256 digest of the exact downloaded bytes.

    Redirects are followed manually (``allow_redirects=False``) so that
    each hop can be validated against ``_ALLOWED_HOSTS`` before the next
    request is issued.  At most ``_MAX_REDIRECTS`` hops are permitted.

    Returns
    -------
    (workbook, sha256_hex)
      workbook    — opened read_only + data_only; caller owns ``wb.close()``.
      sha256_hex  — lowercase hex SHA-256 of the raw downloaded bytes.

    Raises ``IolpFetchError`` on:
    - network / timeout failure at any hop
    - redirect to a disallowed scheme or host
    - too many redirects
    - non-200 final status
    - declared Content-Length exceeds ``_MAX_DOWNLOAD_BYTES``
    - streamed body exceeds ``_MAX_DOWNLOAD_BYTES``
    - body is not a valid ZIP
    - ZIP entry count or total expanded size exceeds limits
    - body is not a valid XLSX workbook

    The exception always carries *url* (the original requested URL) and
    *fetch_attempted_at* so callers can surface provenance even on failure.
    """
    ts = fetch_attempted_at or _now_utc()

    # ------------------------------------------------------------------
    # 1. Manual redirect chain — validate every hop before following it.
    # ------------------------------------------------------------------
    current_url = url
    hop = 0
    while True:
        _validate_redirect_url(current_url, origin_url=url, ts=ts)
        try:
            resp = requests.get(
                current_url,
                timeout=_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            raise IolpFetchError(
                f"Network error fetching IOLP data from {current_url}: {exc}",
                attempted_url=url,
                fetch_attempted_at=ts,
            ) from exc

        if resp.status_code in (301, 302, 303, 307, 308):
            resp.close()
            hop += 1
            if hop > _MAX_REDIRECTS:
                raise IolpFetchError(
                    f"IOLP download from {url} exceeded the "
                    f"{_MAX_REDIRECTS}-redirect limit.",
                    attempted_url=url,
                    fetch_attempted_at=ts,
                )
            location = resp.headers.get("Location", "")
            if not location:
                raise IolpFetchError(
                    f"IOLP redirect response from {current_url} has no "
                    f"Location header.",
                    attempted_url=url,
                    fetch_attempted_at=ts,
                )
            current_url = location
            continue
        break   # non-redirect response — proceed below

    # ------------------------------------------------------------------
    # 2. Check final HTTP status — read only a small prefix for errors.
    # ------------------------------------------------------------------
    if resp.status_code != 200:
        try:
            preview = next(resp.iter_content(chunk_size=512), b"")
            preview_str = preview.decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            preview_str = ""
        finally:
            resp.close()
        raise IolpFetchError(
            f"IOLP download failed with HTTP {resp.status_code}: {preview_str}",
            attempted_url=url,
            fetch_attempted_at=ts,
        )

    # ------------------------------------------------------------------
    # 3. Reject oversized declared Content-Length before streaming.
    # ------------------------------------------------------------------
    declared = resp.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_int = int(declared)
        except ValueError:
            declared_int = None   # non-integer: treat as absent
        if declared_int is not None and declared_int > _MAX_DOWNLOAD_BYTES:
            resp.close()
            raise IolpFetchError(
                f"IOLP file at {url} declares {declared_int:,} bytes, "
                f"exceeding the {_MAX_DOWNLOAD_BYTES:,}-byte limit.",
                attempted_url=url,
                fetch_attempted_at=ts,
            )

    # ------------------------------------------------------------------
    # 4. Stream body, hash simultaneously, enforce byte cap.
    # ------------------------------------------------------------------
    hasher = hashlib.sha256()
    chunks: list[bytes] = []
    received = 0
    try:
        for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
            received += len(chunk)
            if received > _MAX_DOWNLOAD_BYTES:
                resp.close()
                raise IolpFetchError(
                    f"IOLP file at {url} exceeded the "
                    f"{_MAX_DOWNLOAD_BYTES:,}-byte streaming limit "
                    f"(at least {received:,} bytes received).",
                    attempted_url=url,
                    fetch_attempted_at=ts,
                )
            hasher.update(chunk)
            chunks.append(chunk)
    except requests.RequestException as exc:
        raise IolpFetchError(
            f"Network error while streaming IOLP data from {url}: {exc}",
            attempted_url=url,
            fetch_attempted_at=ts,
        ) from exc
    finally:
        resp.close()   # harmless if already closed above

    sha256_hex = hasher.hexdigest()
    body = b"".join(chunks)

    # ------------------------------------------------------------------
    # 5. ZIP pre-validation (entry count + declared expanded sizes).
    # ------------------------------------------------------------------
    _validate_zip(body, url, ts)

    # ------------------------------------------------------------------
    # 6. Open workbook read-only; caller owns wb.close().
    # ------------------------------------------------------------------
    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(body), read_only=True, data_only=True
        )
    except Exception as exc:  # noqa: BLE001
        raise IolpFetchError(
            f"Response from {url} is not a valid XLSX file: {exc}",
            attempted_url=url,
            fetch_attempted_at=ts,
        ) from exc

    return wb, sha256_hex


def _sheet_to_records(
    wb: openpyxl.Workbook,
    dataset: str,
    required_cols: frozenset[str],
) -> tuple[list[dict[str, Any]], bool]:
    """Convert the active worksheet into a list of dicts.

    Returns
    -------
    (records, truncated)
      records   — list of row dicts; each has ``_dataset`` injected.
      truncated — True when _MAX_ROWS was reached before exhausting the sheet.

    Raises IolpClientError when:
      - the workbook has no active sheet
      - the sheet is empty
      - declared sheet dimensions exceed _MAX_CELLS
      - required columns are absent (schema change)
    """
    ws = wb.active
    if ws is None:
        wb.close()
        raise IolpClientError(f"IOLP {dataset} workbook has no active sheet.")

    # Guard against extreme sheet dimensions before iterating rows.
    # openpyxl may report None for max_row/max_column on read_only workbooks
    # that have not yet been iterated; only apply the check when both values
    # are available and positive.
    max_r = ws.max_row
    max_c = ws.max_column
    if max_r and max_c and max_r > 0 and max_c > 0:
        if max_r * max_c > _MAX_CELLS:
            wb.close()
            raise IolpClientError(
                f"IOLP {dataset} sheet declares {max_r:,} rows × "
                f"{max_c:,} columns = {max_r * max_c:,} cells, "
                f"exceeding the {_MAX_CELLS:,}-cell limit."
            )

    rows_iter = ws.iter_rows(values_only=True)
    try:
        raw_headers = next(rows_iter)
    except StopIteration:
        wb.close()
        raise IolpClientError(f"IOLP {dataset} sheet is empty (no header row).")

    headers = [str(h).strip() if h is not None else "" for h in raw_headers]

    missing = required_cols - set(headers)
    if missing:
        wb.close()
        raise IolpClientError(
            f"IOLP {dataset} sheet is missing expected columns: "
            f"{sorted(missing)}. The dataset schema may have changed."
        )

    records = []
    truncated = False
    for i, row in enumerate(rows_iter):
        if i >= _MAX_ROWS:
            truncated = True
            break
        rec: dict[str, Any] = dict(zip(headers, row))
        rec["_dataset"] = dataset
        records.append(rec)

    wb.close()
    return records, truncated


def _matches_query(rec: dict[str, Any], q_lower: str) -> bool:
    """Return True when any searchable text field contains *q_lower*."""
    searchable = (
        "Real Property Asset Name",
        "Installation Name",
        "Street Address",
        "City",
        "State",
        "Zip Code",
    )
    for field in searchable:
        val = rec.get(field)
        if val and q_lower in str(val).lower():
            return True
    return False


def _to_str(val: Any) -> str | None:
    """Coerce a cell value to stripped str, or None if absent."""
    return str(val).strip() if val is not None else None


def _to_float(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(val: Any) -> int | None:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_properties(
    q: str,
    dataset: Literal["leases", "buildings"] | None = None,
    state: str | None = None,
    city: str | None = None,
    building_status: str | None = None,
    owned_or_leased: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    """Search IOLP properties across leases and/or buildings datasets.

    Returns a dict with keys:
      ``total``             — total matching records after all filters
                              (partial when truncated is True)
      ``returned``          — records in this page
      ``records``           — list of raw row dicts (each has ``_dataset``)
      ``fetch_attempted_at`` — UTC timestamp of the first HTTP attempt
      ``retrieved_at_utc``  — same as fetch_attempted_at when all downloads
                              succeeded; null if any fetch failed before parsing
      ``sources_attempted`` — XLSX URLs requested (in order)
      ``sources_retrieved`` — XLSX URLs successfully downloaded and parsed
      ``sources_sha256``    — SHA-256 hex digests of downloaded bytes,
                              parallel to sources_retrieved
      ``truncated``         — True when row_limit was reached in any dataset
      ``records_scanned``   — rows processed across all datasets
      ``row_limit``         — per-dataset row cap applied during this call
    """
    which = (dataset or "both").lower()
    records: list[dict[str, Any]] = []
    sources_attempted: list[str] = []
    sources_retrieved: list[str] = []
    sources_sha256: list[str] = []
    truncated = False
    records_scanned = 0
    fetch_attempted_at = _now_utc()

    if which in ("leases", "both"):
        sources_attempted.append(LEASES_URL)
        wb, sha256 = _fetch_xlsx(LEASES_URL)
        ds_records, ds_truncated = _sheet_to_records(wb, "leases", _REQUIRED_LEASES_COLS)
        records.extend(ds_records)
        records_scanned += len(ds_records)
        if ds_truncated:
            truncated = True
        sources_retrieved.append(LEASES_URL)
        sources_sha256.append(sha256)

    if which in ("buildings", "both"):
        sources_attempted.append(BUILDINGS_URL)
        wb, sha256 = _fetch_xlsx(BUILDINGS_URL)
        ds_records, ds_truncated = _sheet_to_records(wb, "buildings", _REQUIRED_BUILDINGS_COLS)
        records.extend(ds_records)
        records_scanned += len(ds_records)
        if ds_truncated:
            truncated = True
        sources_retrieved.append(BUILDINGS_URL)
        sources_sha256.append(sha256)

    q_lower = q.lower()
    filtered = [r for r in records if _matches_query(r, q_lower)]

    # Optional field filters (case-insensitive exact match)
    if state:
        s = state.strip().upper()
        filtered = [r for r in filtered if (_to_str(r.get("State")) or "").upper() == s]
    if city:
        c = city.strip().upper()
        filtered = [r for r in filtered if (_to_str(r.get("City")) or "").upper() == c]
    if building_status and which != "leases":
        bs = building_status.strip().upper()
        filtered = [
            r for r in filtered
            if r["_dataset"] == "leases"
            or (_to_str(r.get("Building Status")) or "").upper() == bs
        ]
    if owned_or_leased and which != "leases":
        ol = owned_or_leased.strip().upper()
        filtered = [
            r for r in filtered
            if r["_dataset"] == "leases"
            or (_to_str(r.get("Owned or Leased")) or "").upper() == ol
        ]

    total = len(filtered)
    page = filtered[offset: offset + limit]

    # retrieved_at_utc equals fetch_attempted_at when all sources
    # were successfully retrieved; expressed as a distinct key so
    # the server can distinguish attempt from confirmed retrieval.
    retrieved_at_utc = (
        fetch_attempted_at
        if len(sources_retrieved) == len(sources_attempted)
        else None
    )

    return {
        "total": total,
        "returned": len(page),
        "records": page,
        "fetch_attempted_at": fetch_attempted_at,
        "retrieved_at_utc": retrieved_at_utc,
        "sources_attempted": sources_attempted,
        "sources_retrieved": sources_retrieved,
        "sources_sha256": sources_sha256,
        "truncated": truncated,
        "records_scanned": records_scanned,
        "row_limit": _MAX_ROWS,
    }


def get_property(
    location_code: str,
    dataset: Literal["leases", "buildings"] | None = None,
    lease_number: str | None = None,
) -> dict[str, Any]:
    """Retrieve a single property record by Location Code.

    Parameters
    ----------
    location_code:
        The IOLP Location Code, e.g. 'CT3414'.  Comparison is
        case-insensitive and strips surrounding whitespace.
    dataset:
        'leases', 'buildings', or None to search both.
    lease_number:
        Optional lease number disambiguator (e.g. 'LCA00229').  When
        provided and the dataset contains multiple rows for the same
        Location Code, only the row matching this lease number is
        returned.  If no row matches, IolpRecordNotFoundError is raised.

    Ambiguity rules
    ---------------
    - Across datasets: if the same code appears in both leases AND buildings
      and ``dataset`` is None, raise IolpAmbiguousRecordError.
    - Within a dataset: if the same code appears in more than one row and
      ``lease_number`` is not supplied, raise IolpAmbiguousRecordError with
      the count and available lease numbers.
    - Truncated scan + exactly one candidate: raise IolpAmbiguousRecordError
      because uniqueness cannot be established without a full scan.

    Returns
    -------
    A raw row dict with ``_dataset``, ``_retrieved_at_utc``, and
    ``_source_file`` injected.

    Raises
    ------
    IolpAmbiguousRecordError
        When more than one record matches (within or across datasets), or
        when the scan was truncated and only one candidate was found (so
        uniqueness cannot be confirmed).
    IolpRecordNotFoundError
        When no match is found in the searched dataset(s).
    IolpClientError
        On network, HTTP, or parse failure.
    """
    code = location_code.strip().upper()
    which = (dataset or "both").lower()

    # candidates: list of (dataset_name, xlsx_url, matching_row_dict)
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    any_truncated = False
    fetch_attempted_at = _now_utc()
    sources_attempted: list[str] = []
    sources_retrieved: list[str] = []
    sources_sha256_map: dict[str, str] = {}   # url -> sha256_hex

    for url, ds in [(LEASES_URL, "leases"), (BUILDINGS_URL, "buildings")]:
        if which not in (ds, "both"):
            continue
        required = (
            _REQUIRED_LEASES_COLS if ds == "leases" else _REQUIRED_BUILDINGS_COLS
        )
        sources_attempted.append(url)
        wb, sha256 = _fetch_xlsx(url)
        rows, ds_truncated = _sheet_to_records(wb, ds, required)
        sources_retrieved.append(url)
        sources_sha256_map[url] = sha256
        if ds_truncated:
            any_truncated = True

        ds_matches = [
            r for r in rows
            if (_to_str(r.get("Location Code")) or "").upper() == code
        ]

        # Apply lease_number filter when caller supplied one.
        if lease_number and ds_matches:
            ln_upper = lease_number.strip().upper()
            ds_matches = [
                r for r in ds_matches
                if (_to_str(r.get("Lease Number")) or "").upper() == ln_upper
            ]
            if not ds_matches:
                # No matching lease number found — but if the scan was truncated
                # we cannot confirm the lease is absent; report incomplete, not
                # definitively not found.
                if ds_truncated:
                    raise IolpClientError(
                        f"Lookup for Location Code '{location_code}' / Lease Number "
                        f"'{lease_number}' in the {ds} dataset is incomplete: the "
                        f"row limit ({_MAX_ROWS:,} rows per dataset) was reached "
                        f"before the full dataset was scanned. The record may exist "
                        f"in the unscanned portion."
                    )
                raise IolpRecordNotFoundError(
                    f"Location Code '{location_code}' exists in the {ds} "
                    f"dataset but no row has Lease Number '{lease_number}'.",
                    fetch_attempted_at=fetch_attempted_at,
                    source_file=url,
                    source_sha256=sources_sha256_map.get(url),
                )

        if len(ds_matches) > 1:
            # Duplicate rows remain after all filters (including any lease_number
            # filter already applied). Disclose truncation when relevant.
            trunc_note = (
                " The scan was also truncated; additional matching rows may exist "
                f"beyond the {_MAX_ROWS:,}-row limit."
                if ds_truncated else ""
            )
            lease_nums = [
                r.get("Lease Number") for r in ds_matches if r.get("Lease Number")
            ]
            detail = (
                f" Lease numbers found: {', '.join(str(n) for n in lease_nums)}."
                if lease_nums else ""
            )
            hint = (
                f" The supplied lease_number='{lease_number}' matched more than one "
                f"row; the dataset contains duplicate records. Contact the data owner."
                if lease_number
                else " Supply a lease_number to identify the specific record needed."
            )
            raise IolpAmbiguousRecordError(
                f"Location Code '{location_code}' matched {len(ds_matches)} "
                f"rows in the {ds} dataset.{detail}{trunc_note}{hint}",
                candidates=ds_matches,
                fetch_attempted_at=fetch_attempted_at,
                source_file=url,
                source_sha256=sources_sha256_map.get(url),
            )

        candidates.extend((ds, url, r) for r in ds_matches)

    if len(candidates) > 1:
        datasets_found = [c[0] for c in candidates]
        # Cross-dataset ambiguity: multiple sources retrieved; source_file is not
        # a single deterministic URL, so we leave it None per the provenance spec.
        raise IolpAmbiguousRecordError(
            f"Location Code '{location_code}' was found in both datasets "
            f"({', '.join(datasets_found)}). "
            f"Re-request with an explicit dataset= parameter to select the "
            f"intended record.",
            candidates=[c[2] for c in candidates],
            fetch_attempted_at=fetch_attempted_at,
            source_file=None,
            source_sha256=None,
        )

    if not candidates:
        # Only report definitively not-found when the scan was complete.
        # If the row limit was reached, we cannot rule out a match in the
        # unscanned portion, so we raise a generic IolpClientError instead.
        if any_truncated:
            searched = which if which != "both" else "leases and buildings"
            raise IolpClientError(
                f"Lookup for Location Code '{location_code}' in the "
                f"{searched} dataset(s) is incomplete: the row limit "
                f"({_MAX_ROWS:,} rows per dataset) was reached before "
                f"the full dataset was scanned. The record may exist in "
                f"the unscanned portion. Refine the search or contact "
                f"the data owner."
            )
        searched = which if which != "both" else "leases and buildings"
        # Provide the last successfully retrieved source for provenance; if
        # nothing was retrieved, both fields remain None.
        last_retrieved = sources_retrieved[-1] if sources_retrieved else None
        raise IolpRecordNotFoundError(
            f"No IOLP property found with Location Code '{location_code}' "
            f"in the {searched} dataset(s).",
            fetch_attempted_at=fetch_attempted_at,
            source_file=last_retrieved,
            source_sha256=sources_sha256_map.get(last_retrieved) if last_retrieved else None,
        )

    # Truncated scan with exactly one candidate: uniqueness cannot be confirmed
    # because unseen rows may also match the Location Code.
    if any_truncated and len(candidates) == 1:
        ln_hint = (
            f" Supply lease_number='{candidates[0][2].get('Lease Number')}' "
            f"once the full dataset is accessible to confirm the record."
            if candidates[0][2].get("Lease Number") else
            " Use lease_number or dataset= to narrow the lookup once the full "
            "dataset is accessible."
        )
        _trunc_url = candidates[0][1]
        raise IolpAmbiguousRecordError(
            f"Lookup for Location Code '{location_code}' is incomplete: "
            f"the row limit ({_MAX_ROWS:,} rows per dataset) was reached "
            f"before a full scan. One candidate was found but uniqueness "
            f"cannot be established.{ln_hint}",
            candidates=[candidates[0][2]],
            fetch_attempted_at=fetch_attempted_at,
            source_file=_trunc_url,
            source_sha256=sources_sha256_map.get(_trunc_url),
        )

    ds_found, url_found, rec = candidates[0]
    retrieved_at_utc = (
        fetch_attempted_at
        if len(sources_retrieved) == len(sources_attempted)
        else None
    )
    rec["_fetch_attempted_at"] = fetch_attempted_at
    rec["_retrieved_at_utc"] = retrieved_at_utc
    rec["_source_file"] = url_found
    rec["_source_sha256"] = sources_sha256_map.get(url_found)
    return rec
