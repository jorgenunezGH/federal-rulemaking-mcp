"""Typed input/output models for MCP tools."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SearchInput(BaseModel):
    """Input for search_rulemaking_documents."""
    search_term: str = Field(
        ..., min_length=1, description="Keyword or phrase to search for"
    )
    agency_id: str | None = Field(
        None, description="Optional agency filter, e.g. 'EERE'"
    )
    document_type: str | None = Field(
        None, description="Optional type, e.g. 'Proposed Rule'"
    )
    page_size: int = Field(
        10, ge=5, le=250, description="Results per page (min 5, max 250)"
    )


class DocumentResult(BaseModel):
    """A single search result."""
    document_id: str
    title: str
    document_type: str | None = None
    posted_date: str | None = None
    source_url: str


class SearchOutput(BaseModel):
    """Output for search_rulemaking_documents."""
    total_found: int
    results: list[DocumentResult]
    message: str | None = None

# Document IDs are agency-prefixed and hyphen-delimited, but the segment
# count varies by agency — compare FDA-2021-N-0270-0001 (5 segments) with
# EERE-2010-BT-STD-0031-0128 (6). The pattern therefore requires at least
# three segments rather than a fixed count, rejecting obvious garbage
# without encoding assumptions about any single agency's convention.
DOCUMENT_ID_PATTERN = r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}$"


class DocumentDetailInput(BaseModel):
    """Input for get_document_details."""

    document_id: str = Field(
        ...,
        min_length=5,
        pattern=DOCUMENT_ID_PATTERN,
        description=(
            "Regulations.gov document identifier, "
            "for example 'FDA-2021-N-0270-0001'"
        ),
    )

class DocumentDetailOutput(BaseModel):
    """Agent-facing metadata for a single rulemaking document.

    Flattens the API's nested JSON:API structure into the fields an
    analyst actually needs, and always carries a source_url so any
    downstream summary can cite the authoritative record.
    """

    document_id: str
    title: str
    document_type: str | None = None
    subtype: str | None = None
    agency_id: str | None = None
    docket_id: str | None = None
    posted_date: str | None = None
    fr_document_number: str | None = None
    fr_citation: str | None = None
    page_count: int | None = None
    withdrawn: bool | None = None
    open_for_comment: bool | None = None
    within_comment_period: bool | None = None
    comment_end_date: str | None = None
    source_url: str
    message: str | None = None

# Docket IDs are shorter than document IDs — a document ID is its parent
# docket plus a suffix (FDA-2021-N-0270-0001 belongs to FDA-2021-N-0270).
# Two or more segments is therefore the correct minimum here, versus three
# for documents.
DOCKET_ID_PATTERN = r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+){1,}$"


class DocketDetailInput(BaseModel):
    """Input for get_docket_details."""

    docket_id: str = Field(
        ...,
        min_length=3,
        pattern=DOCKET_ID_PATTERN,
        description=(
            "Regulations.gov docket identifier, "
            "for example 'FDA-2021-N-0270'"
        ),
    )


class DocketDetailOutput(BaseModel):
    """Agent-facing metadata for a rulemaking docket.

    Surfaces docket_type prominently because it distinguishes an actual
    rulemaking, which may create compliance obligations, from a
    non-rulemaking notice, which is informational. That distinction drives
    how an analyst prioritizes review.
    """

    docket_id: str
    title: str
    short_title: str | None = None
    agency_id: str | None = None
    program: str | None = None
    docket_type: str | None = None
    is_rulemaking: bool | None = None
    rin: str | None = None
    keywords: list[str] = Field(default_factory=list)
    effective_date: str | None = None
    modify_date: str | None = None
    source_url: str
    message: str | None = None

class CommentStatus(str, Enum):
    """Resolved comment period status.

    The Regulations.gov API exposes several flags that can appear to
    contradict one another — openForComment may be true while
    withinCommentPeriod is false, because late comments are permitted on
    a window that has already closed. This enum collapses those flags
    into one unambiguous state an agent can act on.
    """

    OPEN = "open"
    CLOSED_LATE_ACCEPTED = "closed_late_comments_accepted"
    CLOSED = "closed"
    NOT_YET_OPEN = "not_yet_open"
    NOT_APPLICABLE = "not_applicable"
    WITHDRAWN = "withdrawn"


class CommentRequirementsInput(BaseModel):
    """Input for get_comment_requirements."""

    document_id: str = Field(
        ...,
        min_length=5,
        pattern=DOCUMENT_ID_PATTERN,
        description=(
            "Regulations.gov document identifier to check, "
            "for example 'EERE-2022-BT-TP-0028-0021'"
        ),
    )

class CommentRequirementsOutput(BaseModel):
    """Resolved comment period status for a rulemaking document.

    Provides both a machine-readable status and a plain-language summary
    so the agent can either branch on the enum or quote the summary
    directly into an analyst briefing.
    """

    document_id: str
    title: str
    status: CommentStatus
    summary: str
    accepting_comments: bool
    comment_start_date: str | None = None
    comment_end_date: str | None = None
    days_remaining: int | None = None
    days_since_closed: int | None = None
    allows_late_comments: bool | None = None
    withdrawn: bool | None = None
    source_url: str
    comment_url: str | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# IOLP — Inventory of Owned and Leased Properties
# Source: https://catalog.data.gov/dataset/inventory-of-owned-and-leased-properties-iolp
# Two XLSX datasets: "leases" and "buildings" (each ~7–8k rows).
# ---------------------------------------------------------------------------

# Valid dataset identifiers shared by search and detail tools.
IolpDataset = Literal["leases", "buildings"]


class IolpSearchInput(BaseModel):
    """Input for search_federal_properties."""

    q: str = Field(
        ...,
        min_length=1,
        description=(
            "Keyword or phrase to search in property name, address, city, "
            "or state (case-insensitive substring match)."
        ),
    )
    dataset: IolpDataset | None = Field(
        None,
        description=(
            "Which dataset to search: 'leases', 'buildings', or omit for both."
        ),
    )
    state: str | None = Field(
        None,
        description="Two-letter US state abbreviation filter, e.g. 'VA'.",
    )
    city: str | None = Field(
        None,
        description="City name filter (case-insensitive exact match), e.g. 'BOSTON'.",
    )
    building_status: str | None = Field(
        None,
        description=(
            "Buildings-only status filter, e.g. 'ACTIVE' or 'INACTIVE'. "
            "Ignored when dataset='leases'."
        ),
    )
    owned_or_leased: str | None = Field(
        None,
        description=(
            "Buildings-only ownership filter: 'OWNED', 'LEASED', etc. "
            "Ignored when dataset='leases'."
        ),
    )
    limit: int = Field(
        10,
        ge=1,
        le=100,
        description="Maximum number of results to return (1–100, default 10).",
    )
    offset: int = Field(
        0,
        ge=0,
        description="Number of matching records to skip for pagination (default 0).",
    )


class IolpPropertyRecord(BaseModel):
    """A single property result row from the IOLP dataset."""

    location_code: str | None = None
    real_property_asset_name: str | None = None
    dataset: str  # 'leases' or 'buildings'
    street_address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    gsa_region: int | None = None
    real_property_asset_type: str | None = None
    building_rentable_sq_ft: float | None = None
    # buildings-only
    owned_or_leased: str | None = None
    building_status: str | None = None
    # leases-only
    lease_number: str | None = None
    lease_effective_date: str | None = None
    lease_expiration_date: str | None = None
    source_url: str


class IolpSearchOutput(BaseModel):
    """Output for search_federal_properties.

    Provenance contract
    -------------------
    source_url          — catalog landing page; always present.

    sources_attempted   — XLSX URLs for which an HTTP request was issued.
                          Empty when validation failed before any request.
    sources_retrieved   — XLSX URLs successfully downloaded AND parsed.
                          A URL may be attempted but not retrieved (e.g. on
                          HTTP error or invalid workbook).

    fetch_attempted_at  — UTC timestamp of the first HTTP attempt.
                          Null when no request was made (validation failure).
    retrieved_at_utc    — UTC timestamp of first attempt when ALL sources were
                          successfully retrieved; null otherwise.  Do not use
                          this as a retrieval timestamp when any source failed.

    truncated           — True when the per-dataset row_limit was reached;
                          total_found and result rankings are then partial.
    row_limit           — per-dataset row cap in effect; null when no scan ran.
    records_scanned     — rows processed across all datasets in this call.
    """

    total_found: int
    returned: int
    results: list[IolpPropertyRecord]
    source_url: str
    sources_attempted: list[str]      # empty when no HTTP was attempted
    sources_retrieved: list[str]      # empty when retrieval failed
    sources_sha256: list[str] = Field(
        default_factory=list,
        description=(
            "SHA-256 hex digests of the exact bytes downloaded for each "
            "successfully retrieved source, in the same order as "
            "sources_retrieved. Empty when no source was retrieved."
        ),
    )
    fetch_attempted_at: str | None    # null when no HTTP was attempted
    retrieved_at_utc: str | None      # null when not all sources retrieved
    truncated: bool = False
    row_limit: int | None = None
    records_scanned: int | None = None
    message: str | None = None


class IolpPropertyDetailInput(BaseModel):
    """Input for get_property_details."""

    location_code: str = Field(
        ...,
        min_length=2,
        description=(
            "IOLP Location Code that uniquely identifies the property, "
            "for example 'CT3414' or 'MA6048'."
        ),
    )
    dataset: IolpDataset | None = Field(
        None,
        description=(
            "Which dataset to search: 'leases', 'buildings', or omit to "
            "search both. When the same Location Code appears in more than "
            "one dataset, supply this field to select the intended record."
        ),
    )
    lease_number: str | None = Field(
        None,
        description=(
            "Optional lease number to disambiguate when a Location Code "
            "maps to multiple leases, e.g. 'LCA00229'. When supplied and "
            "the dataset contains multiple rows for the Location Code, only "
            "the row with this Lease Number is returned."
        ),
    )


class IolpPropertyDetailOutput(BaseModel):
    """Full record for a single IOLP property.

    congressional_district is included as-is from the XLSX.
    The representative name field has been omitted: property records do not
    constitute an authoritative or current source for that information.
    """

    location_code: str | None = None
    real_property_asset_name: str | None = None
    installation_name: str | None = None
    # 'leases' or 'buildings' — identifies which XLSX the record came from.
    dataset: str
    street_address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    gsa_region: int | None = None
    real_property_asset_type: str | None = None
    building_rentable_sq_ft: float | None = None
    available_sq_ft: float | None = None
    congressional_district: str | None = None
    # buildings-only
    owned_or_leased: str | None = None
    building_status: str | None = None
    construction_date: int | None = None
    # leases-only
    federal_leased_code: str | None = None
    lease_number: str | None = None
    lease_effective_date: str | None = None
    lease_expiration_date: str | None = None
    # Provenance contract (mirrors IolpSearchOutput):
    #   source_url         — catalog landing page; always present.
    #   source_file        — exact XLSX URL parsed; null when no fetch occurred.
    #   fetch_attempted_at — UTC timestamp of first HTTP attempt; null on
    #                        validation failure or pre-fetch error.
    #   retrieved_at_utc   — same as fetch_attempted_at when retrieval and
    #                        parsing both succeeded; null otherwise.
    source_url: str
    source_file: str | None           # null on validation failure / pre-fetch error
    source_sha256: str | None = None  # hex SHA-256 of XLSX bytes; null when no fetch
    fetch_attempted_at: str | None    # null on validation failure / pre-fetch error
    retrieved_at_utc: str | None      # null when retrieval or parsing did not complete
    message: str | None = None
