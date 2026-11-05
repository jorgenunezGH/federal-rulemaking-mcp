# IOLP Integration Plan

## Top-Level Overview

Add two new MCP tools to the `federal-rulemaking-read` server that expose the
**Inventory of Owned and Leased Properties (IOLP)** dataset from
`catalog.data.gov`.

**Discovery findings (Sub-Task 1 complete):**
The IOLP dataset is NOT backed by a live CKAN Datastore. The data is
distributed as two XLSX files on `inventory.data.gov` (redirecting from S3):

| Dataset | Resource ID | Rows | Columns |
|---------|-------------|------|---------|
| Leases  | `13886719-6b8a-4596-8c82-5b32664005f7` | 7,078 | 19 |
| Buildings (owned + leased) | `ad1a66f2-8019-43d9-b625-ea55e1f09677` | 7,746 | 18 |

**Implementation approach:** The client fetches the relevant XLSX(es) via HTTP,
parses with `openpyxl`, and filters/searches in memory. `openpyxl` must be
added to `requirements.txt`.

**XLSX Download URLs:**
- Leases: `https://inventory.data.gov/dataset/9a9e946e-124e-46f5-a934-e458a6c1c2b2/resource/13886719-6b8a-4596-8c82-5b32664005f7/download/2026-10-30-iolp-leases.xlsx`
- Buildings: `https://inventory.data.gov/dataset/9a9e946e-124e-46f5-a934-e458a6c1c2b2/resource/ad1a66f2-8019-43d9-b625-ea55e1f09677/download/2026-10-30-iolp-buildings.xlsx`

**LEASES columns (19):** Location Code, Real Property Asset Name, Installation
Name, Federal Leased Code, GSA Region, Street Address, City, State, Zip Code,
Latitude, Longitude, Building Rentable Square Feet, Available Square Feet,
Congressional District, Congressional District Representative, Lease Number,
Lease Effective Date, Lease Expiration Date, Real Property Asset type

**BUILDINGS columns (18):** Location Code, Real Property Asset Name,
Installation Name, Owned or Leased, GSA Region, Street Address, City, State,
Zip Code, Latitude, Longitude, Building Rentable Square Feet, Available Square
Feet, Construction Date, Congressional District, Congressional District
Representative Name, Building Status, Real Property Asset Type

**Common filterable fields across both datasets:** State, City, Zip Code,
Real Property Asset Name (text search), Real Property Asset Type/type

**Leases-only filters:** Federal Leased Code, Lease Number, Lease Expiration Date

**Buildings-only filters:** Owned or Leased, Building Status

The new tools follow the same four-layer pattern already used for Regulations.gov:

```
server.py  →  src/clients/iolp_client.py  →  src/models.py
```

Scope:
- **`search_federal_properties`** — search across both datasets by keyword
  (matches name, city, state, address) with optional filters for: `dataset`
  (leases/buildings), `state`, `city`, `building_status`,
  `owned_or_leased`. Returns a list of matching records with `source_url`.
- **`get_property_details`** — fetch a single property by `location_code`
  (e.g. "CT3414"), optionally narrowed by `dataset` and `lease_number`.

Non-goals: no writes, no persistent caching, no bulk exports.

---

## Sub-Tasks

---

### Sub-Task 1 — Discover the IOLP CKAN resource ID and field schema

**Status:** [x] done

**Discovered Values**
```
dataset        = inventory-of-owned-and-leased-properties-iolp
parent_id      = 9a9e946e-124e-46f5-a934-e458a6c1c2b2
leases_id      = 13886719-6b8a-4596-8c82-5b32664005f7  (7,078 rows, 19 cols)
buildings_id   = ad1a66f2-8019-43d9-b625-ea55e1f09677  (7,746 rows, 18 cols)
access         = XLSX download via inventory.data.gov -> S3 redirect (no auth)
approach       = fetch XLSX, parse with openpyxl, filter in memory

LEASES fields:  Location Code, Real Property Asset Name, Installation Name,
  Federal Leased Code, GSA Region, Street Address, City, State, Zip Code,
  Latitude, Longitude, Building Rentable Square Feet, Available Square Feet,
  Congressional District, Congressional District Representative,
  Lease Number, Lease Effective Date, Lease Expiration Date,
  Real Property Asset type

BUILDINGS fields: Location Code, Real Property Asset Name, Installation Name,
  Owned or Leased, GSA Region, Street Address, City, State, Zip Code,
  Latitude, Longitude, Building Rentable Square Feet, Available Square Feet,
  Construction Date, Congressional District,
  Congressional District Representative Name, Building Status,
  Real Property Asset Type

sample_lease = {Location Code: CT3414, Real Property Asset Name: 2 UNION PLAZA,
  State: CT, City: NEW LONDON, Street Address: 2 UNION PLZ,
  Lease Number: LCT00005, Lease Effective Date: 7/27/2019,
  Lease Expiration Date: 7/26/2034}

sample_building = {Location Code: MA6048, Real Property Asset Name: ICE HSI BOSTON,
  State: MA, City: BOSTON, Street Address: 580 CHELSEA STREET,
  Owned or Leased: LEASED, Building Status: ACTIVE}
```

---

### Sub-Task 2 — Add Pydantic models to `src/models.py`

**Status:** [x] done

**What was implemented**
- `IolpDataset = Literal["leases", "buildings"]` — constrains dataset param;
  `"both"` and any other value are rejected by Pydantic before any HTTP call.
- `IolpSearchInput` — `q`, `dataset`, `state`, `city`, `building_status`,
  `owned_or_leased`, `limit` (1–100), `offset`.
- `IolpPropertyRecord` — single search result row; lease and buildings fields
  both present (null when not applicable); includes `source_url`.
- `IolpSearchOutput` — `total_found`, `returned`, `results`, full provenance
  contract: `source_url`, `sources_attempted`, `sources_retrieved`,
  `fetch_attempted_at`, `retrieved_at_utc`, `truncated`, `row_limit`,
  `records_scanned`, `message`.
- `IolpPropertyDetailInput` — `location_code` (min_length=2), `dataset`,
  `lease_number` (optional disambiguator).
- `IolpPropertyDetailOutput` — all IOLP columns; `congressional_district_representative`
  omitted (not an authoritative source for that field); includes
  `source_url`, `source_file`, `fetch_attempted_at`, `retrieved_at_utc`.

---

### Sub-Task 3 — Add IOLP HTTP client `src/clients/iolp_client.py`

**Status:** [x] done

**What was implemented**
- `IolpClientError` — base exception.
- `IolpFetchError(IolpClientError)` — raised by `_fetch_xlsx` on network/HTTP/
  parse failure; carries `attempted_url` and `fetch_attempted_at` so the server
  layer can surface provenance even on error.
- `IolpRecordNotFoundError(IolpClientError)` — definitively absent (complete scan only).
- `IolpAmbiguousRecordError(IolpClientError)` — carries `candidates` list;
  raised for within-dataset duplicates, cross-dataset matches, and truncated
  scans where uniqueness cannot be confirmed.
- `_fetch_xlsx(url)` — downloads and parses XLSX; raises `IolpFetchError` on
  any failure.
- `_sheet_to_records(wb, dataset, required_cols)` — returns `(records, truncated: bool)`.
  Validates required columns; caps at `_MAX_ROWS = 50_000` per dataset.
- `search_properties(...)` — fetches relevant XLSX(es), filters in memory,
  returns metadata dict including `row_limit`, `records_scanned`,
  `sources_attempted`, `sources_retrieved`, `fetch_attempted_at`,
  `retrieved_at_utc`, `truncated`.
- `get_property(location_code, dataset, lease_number)` — full ambiguity matrix:

  | Scan / result | Outcome |
  |---|---|
  | Complete, one match | Return the record |
  | Complete, no match | `IolpRecordNotFoundError` |
  | Complete, multiple matches | `IolpAmbiguousRecordError` + lease numbers listed |
  | Complete, `lease_number` resolves to one row | Return the record |
  | Complete, `lease_number` resolves to multiple rows | `IolpAmbiguousRecordError` — duplicate data, contact owner |
  | Truncated, no match | `IolpClientError` — incomplete scan |
  | Truncated, one match | `IolpAmbiguousRecordError` — uniqueness unestablished |
  | Truncated, multiple matches | `IolpAmbiguousRecordError` + truncation disclosed |
  | Truncated + `lease_number`, no match | `IolpClientError` — incomplete scan |
  | Truncated + `lease_number`, multiple matches | `IolpAmbiguousRecordError` + truncation disclosed |

---

### Sub-Task 4 — Register the two new tools in `src/server.py`

**Status:** [x] done

**What was implemented**
- `IOLP_DATASET_URL` constant: catalog landing page.
- `search_federal_properties(q, dataset, state, city, building_status, owned_or_leased, limit, offset)` — validates via `IolpSearchInput`, calls `search_properties`, maps records to `IolpPropertyRecord` list, returns `IolpSearchOutput.model_dump()`.
- `get_property_details(location_code, dataset, lease_number)` — validates via `IolpPropertyDetailInput`, calls `get_property`, maps to `IolpPropertyDetailOutput.model_dump()`.
- Both tools import and check `isinstance(e, IolpFetchError)` in their `except IolpClientError` blocks to preserve `attempted_url` and `fetch_attempted_at` on fetch failures.
- `row_limit` from client metadata is read at `raw["row_limit"]` (not imported as a constant from the client module — it lives in the client layer only).

---

### Sub-Task 5 — Write unit tests

**Status:** [x] done

**Test counts**
- `tests/test_iolp_search.py` — 26 tests across 9 evidence checks
- `tests/test_iolp_details.py` — 37 tests across 13 evidence checks
- Total new: 63 tests; all offline (HTTP mocked at `src.clients.iolp_client.requests.get`)
- Pre-existing tests: 85 (all still passing)
- **Total: 124 passed, 0 failures**

**Evidence checks covered**

*search:*
1. Keyword match — name, address, city fields
2. Dataset filter restricts HTTP calls to one file
3. State + city filters applied correctly
4. Buildings-only filters (building_status, owned_or_leased)
5. Pagination (offset + limit)
6. `dataset="both"` rejected by Pydantic before HTTP
7. Empty result returns message
8. Row limit hit → truncated flag + partial-results message
9. Missing required column → explicit schema error

*details:*
1. Within-dataset duplicate → ambiguity with lease numbers listed
2. Cross-dataset ambiguity → resolved by explicit `dataset=`
3. Explicit `dataset=` restricts fetch to one HTTP call
4. Invalid `dataset` value → Pydantic rejection before HTTP
5. Validation failure → null `fetch_attempted_at`, null `retrieved_at_utc`
6. `source_file` is the actual XLSX URL, not the catalog page
7. Row limit hit, no match → incomplete error (not "not found")
8. Happy path leases + buildings → correct fields and provenance
9. Non-200 / invalid XLSX → `fetch_attempted_at` is a UTC ISO timestamp
10. Truncated scan + 1 match → `IolpAmbiguousRecordError`
11. `lease_number` resolves duplicate; wrong lease number returns error
12. Fetch error → `fetch_attempted_at` is a UTC ISO timestamp (regex-verified)
13. Full behavior matrix — `lease_number` × truncation combinations (4 cases)

---

## Open items (not closed by this task)

| Item | Status |
|---|---|
| Live latency characterisation | One measurement: 7.36 s (leases, LA filter). Breakdown by phase not captured. Not resolved. |
| Controlled agent test | Not run. See canonical call below. |
| Cloud deployment | Changes are uncommitted; deployed server does not contain IOLP tools. |
| Evaluation scenario counts | Not incremented. Live correctness and agent routing are separate acceptance gates. |

**Canonical agent test call:**
```
get_property_details(
    location_code="CA6395",
    dataset="leases",
    lease_number="LCA00229"
)
```
Without `lease_number=`, the tool returns an ambiguity message listing the 5
lease numbers at that location code (LCA00229, LCA02838, LCA02892, LCA02972,
LCA03313). The agent must surface the ambiguity and request a selection rather
than inventing a single record.

---

## Architecture Diagram

```
Agent (MCP client)
        │
        ▼
src/server.py
  ├── search_federal_properties()   ← new
  └── get_property_details()        ← new
        │
        ▼
src/clients/iolp_client.py          ← new
  ├── search_properties()
  └── get_property()
        │
        ▼
inventory.data.gov (XLSX via HTTP)
  ├── 2026-10-30-iolp-leases.xlsx    (~7,078 rows)
  └── 2026-10-30-iolp-buildings.xlsx (~7,746 rows)
```
