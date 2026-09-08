"""Typed input/output models for MCP tools."""
from enum import Enum

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