"""Federal Rulemaking Read MCP Server (MCP Python SDK v2).

Transport layer only. Analytical logic lives in src/comment_status.py and
HTTP interaction lives in src/clients/regulations_client.py, so each
concern can be tested and reviewed in isolation.
"""

from datetime import datetime, timezone

try:
    # mcp 2.x (Orchestrate runtime)
    from mcp.server.mcpserver import MCPServer
except ImportError:
    # mcp 1.x (local venv)
    from mcp.server.fastmcp import FastMCP as MCPServer  # type: ignore[no-redef]
from pydantic import ValidationError

from src.clients.iolp_client import (
    IolpAmbiguousRecordError,
    IolpClientError,
    IolpFetchError,
    IolpRecordNotFoundError,
    get_property,
    search_properties,
)
from src.clients.regulations_client import (
    DocumentNotFoundError,
    RegulationsClientError,
    get_docket,
    get_document,
    search_documents,
)
from src.comment_status import resolve_comment_status
from src.models import (
    CommentRequirementsInput,
    CommentRequirementsOutput,
    CommentStatus,
    DocketDetailInput,
    DocketDetailOutput,
    DocumentDetailInput,
    DocumentDetailOutput,
    DocumentResult,
    IolpPropertyDetailInput,
    IolpPropertyDetailOutput,
    IolpPropertyRecord,
    IolpSearchInput,
    IolpSearchOutput,
    SearchInput,
    SearchOutput,
)

mcp = MCPServer("federal-rulemaking-read")
app = mcp  # alias used by some transports

# Dockets, documents, and comment forms each use a distinct Regulations.gov
# URL path, so provenance links must be built from the correct base for the
# record type being returned.
REG_GOV_DOC_BASE = "https://www.regulations.gov/document/"
REG_GOV_DOCKET_BASE = "https://www.regulations.gov/docket/"
REG_GOV_COMMENT_BASE = "https://www.regulations.gov/commenton/"

IOLP_DATASET_URL = (
    "https://catalog.data.gov/dataset/inventory-of-owned-and-leased-properties-iolp"
)


@mcp.tool()
def search_rulemaking_documents(
    search_term: str,
    agency_id: str | None = None,
    document_type: str | None = None,
    page_size: int = 10,
) -> dict:
    """Search federal rulemaking documents on Regulations.gov.

    Use this to find proposed rules, notices, and other rulemaking
    documents by keyword, optionally filtered by agency or type.
    Every result includes a source_url for provenance.
    """
    # Validation runs before any network call so a malformed request never
    # consumes rate limit and the agent receives a specific reason.
    try:
        params = SearchInput(
            search_term=search_term,
            agency_id=agency_id,
            document_type=document_type,
            page_size=page_size,
        )
    except ValidationError as e:
        return SearchOutput(
            total_found=0,
            results=[],
            message=f"Invalid search parameters: {e}",
        ).model_dump()

    try:
        raw = search_documents(
            search_term=params.search_term,
            agency_id=params.agency_id,
            document_type=params.document_type,
            page_size=params.page_size,
        )
    except RegulationsClientError as e:
        return SearchOutput(
            total_found=0,
            results=[],
            message=f"Search could not be completed: {e}",
        ).model_dump()

    data = raw.get("data", [])
    total = raw.get("meta", {}).get("totalElements", len(data))

    # An empty result set is a valid answer, not a failure. A clear message
    # prevents the agent from retrying pointlessly.
    if not data:
        return SearchOutput(
            total_found=0,
            results=[],
            message="No matching documents found.",
        ).model_dump()

    results = []
    for doc in data:
        attrs = doc.get("attributes", {})
        results.append(
            DocumentResult(
                document_id=doc.get("id", "unknown"),
                title=attrs.get("title", "No title available"),
                document_type=attrs.get("documentType"),
                posted_date=attrs.get("postedDate"),
                source_url=f"{REG_GOV_DOC_BASE}{doc.get('id', '')}",
            )
        )

    return SearchOutput(total_found=total, results=results).model_dump()


@mcp.tool()
def get_document_details(document_id: str) -> dict:
    """Retrieve full authoritative metadata for a specific rulemaking document.

    Use this after finding a document through search, when the analyst
    needs the complete record: full title, agency, parent docket, Federal
    Register citation, page count, withdrawal status, and current comment
    status. Returns a source_url linking to the authoritative record on
    Regulations.gov.
    """
    # Check the ID shape first. A malformed ID sent upstream returns a 404
    # indistinguishable from a genuinely missing document, which is a worse
    # signal for the agent to reason about.
    try:
        params = DocumentDetailInput(document_id=document_id)
    except ValidationError as e:
        return {
            "document_id": document_id,
            "source_url": "",
            "message": f"Invalid document ID format: {e}",
        }

    try:
        raw = get_document(params.document_id)
    except DocumentNotFoundError as e:
        return {
            "document_id": document_id,
            "source_url": "",
            "message": str(e),
        }
    except RegulationsClientError as e:
        return {
            "document_id": document_id,
            "source_url": "",
            "message": f"Could not retrieve document: {e}",
        }

    data = raw.get("data", {})
    attrs = data.get("attributes", {})
    resolved_id = data.get("id", params.document_id)

    return DocumentDetailOutput(
        document_id=resolved_id,
        title=attrs.get("title", "No title available"),
        document_type=attrs.get("documentType"),
        subtype=attrs.get("subtype"),
        agency_id=attrs.get("agencyId"),
        docket_id=attrs.get("docketId"),
        posted_date=attrs.get("postedDate"),
        fr_document_number=attrs.get("frDocNum"),
        fr_citation=attrs.get("frVolNum"),
        page_count=attrs.get("pageCount"),
        withdrawn=attrs.get("withdrawn"),
        open_for_comment=attrs.get("openForComment"),
        within_comment_period=attrs.get("withinCommentPeriod"),
        comment_end_date=attrs.get("commentEndDate"),
        source_url=f"{REG_GOV_DOC_BASE}{resolved_id}",
    ).model_dump()


@mcp.tool()
def get_docket_details(docket_id: str) -> dict:
    """Retrieve the parent docket for a rulemaking, giving full regulatory context.

    Use this after get_document_details returns a docket_id, when the
    analyst needs to understand the broader regulatory action rather than a
    single filing. A docket may contain many documents over several years.
    Reports whether the docket is an actual rulemaking, which may create
    compliance obligations, or a non-rulemaking notice.
    """
    try:
        params = DocketDetailInput(docket_id=docket_id)
    except ValidationError as e:
        return {
            "docket_id": docket_id,
            "source_url": "",
            "message": f"Invalid docket ID format: {e}",
        }

    try:
        raw = get_docket(params.docket_id)
    except DocumentNotFoundError as e:
        return {
            "docket_id": docket_id,
            "source_url": "",
            "message": str(e),
        }
    except RegulationsClientError as e:
        return {
            "docket_id": docket_id,
            "source_url": "",
            "message": f"Could not retrieve docket: {e}",
        }

    data = raw.get("data", {})
    attrs = data.get("attributes", {})
    resolved_id = data.get("id", params.docket_id)

    # Derive a plain boolean from the API's docketType vocabulary so the
    # agent does not need to know that "Nonrulemaking" is the negative case.
    # Left as None when the field is absent rather than guessing.
    docket_type = attrs.get("docketType")
    is_rulemaking = None
    if docket_type:
        is_rulemaking = docket_type.strip().lower() == "rulemaking"

    # keywords can be absent or null; normalize to a list so the agent
    # always receives a consistent type.
    keywords = attrs.get("keywords") or []

    return DocketDetailOutput(
        docket_id=resolved_id,
        title=attrs.get("title", "No title available"),
        short_title=attrs.get("shortTitle"),
        agency_id=attrs.get("agencyId"),
        program=attrs.get("program"),
        docket_type=docket_type,
        is_rulemaking=is_rulemaking,
        rin=attrs.get("rin"),
        keywords=keywords,
        effective_date=attrs.get("effectiveDate"),
        modify_date=attrs.get("modifyDate"),
        source_url=f"{REG_GOV_DOCKET_BASE}{resolved_id}",
    ).model_dump()


@mcp.tool()
def get_comment_requirements(document_id: str) -> dict:
    """Determine whether a rulemaking document is accepting public comments.

    Use this when the analyst needs a clear answer about comment
    eligibility and deadlines. Resolves the several comment fields the API
    exposes, which can appear to contradict one another, into a single
    status with a plain-language summary suitable for a briefing.

    Reports one of: open, closed with late comments accepted, closed, not
    yet open, withdrawn, or not applicable.
    """
    try:
        params = CommentRequirementsInput(document_id=document_id)
    except ValidationError as e:
        return {
            "document_id": document_id,
            "source_url": "",
            "message": f"Invalid document ID format: {e}",
        }

    # Comment data lives on the document record, so this reuses
    # get_document rather than issuing a separate request.
    try:
        raw = get_document(params.document_id)
    except DocumentNotFoundError as e:
        return {
            "document_id": document_id,
            "source_url": "",
            "message": str(e),
        }
    except RegulationsClientError as e:
        return {
            "document_id": document_id,
            "source_url": "",
            "message": f"Could not retrieve comment requirements: {e}",
        }

    data = raw.get("data", {})
    attrs = data.get("attributes", {})
    resolved_id = data.get("id", params.document_id)

    # Capture a single reference time so every derived date figure in the
    # response is internally consistent.
    now = datetime.now(timezone.utc)

    status, summary, days_remaining, days_since_closed = resolve_comment_status(
        attrs, now
    )

    accepting = status in (
        CommentStatus.OPEN,
        CommentStatus.CLOSED_LATE_ACCEPTED,
    )

    # Only surface a submission link when comments can actually be filed.
    # Offering the link on a closed window would invite a wasted action.
    comment_url = f"{REG_GOV_COMMENT_BASE}{resolved_id}" if accepting else None

    return CommentRequirementsOutput(
        document_id=resolved_id,
        title=attrs.get("title", "No title available"),
        status=status,
        summary=summary,
        accepting_comments=accepting,
        comment_start_date=attrs.get("commentStartDate"),
        comment_end_date=attrs.get("commentEndDate"),
        days_remaining=days_remaining,
        days_since_closed=days_since_closed,
        allows_late_comments=attrs.get("allowLateComments"),
        withdrawn=attrs.get("withdrawn"),
        source_url=f"{REG_GOV_DOC_BASE}{resolved_id}",
        comment_url=comment_url,
    ).model_dump()


@mcp.tool()
def search_federal_properties(
    q: str,
    dataset: str | None = None,
    state: str | None = None,
    city: str | None = None,
    building_status: str | None = None,
    owned_or_leased: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    """Search the federal Inventory of Owned and Leased Properties (IOLP).

    Searches across GSA leases and/or owned buildings by keyword, with
    optional filters for state, city, building status, and ownership type.
    Results include location code, address, square footage, and a source_url
    pointing to the IOLP dataset on catalog.data.gov.

    Args:
        q: Keyword to search in property name, address, city, or state.
        dataset: 'leases', 'buildings', or omit for both.
        state: Two-letter state abbreviation, e.g. 'VA'.
        city: City name filter, e.g. 'BOSTON'.
        building_status: Buildings only — e.g. 'ACTIVE' or 'INACTIVE'.
        owned_or_leased: Buildings only — e.g. 'OWNED' or 'LEASED'.
        limit: Results per page (1–100, default 10).
        offset: Records to skip for pagination (default 0).
    """
    try:
        params = IolpSearchInput(
            q=q,
            dataset=dataset,
            state=state,
            city=city,
            building_status=building_status,
            owned_or_leased=owned_or_leased,
            limit=limit,
            offset=offset,
        )
    except Exception as e:  # pydantic ValidationError
        # Validation failed before any HTTP request — no retrieval occurred.
        return IolpSearchOutput(
            total_found=0,
            returned=0,
            results=[],
            source_url=IOLP_DATASET_URL,
            sources_attempted=[],
            sources_retrieved=[],
            fetch_attempted_at=None,
            retrieved_at_utc=None,
            message=f"Invalid search parameters: {e}",
        ).model_dump()

    try:
        raw = search_properties(
            q=params.q,
            dataset=params.dataset,
            state=params.state,
            city=params.city,
            building_status=params.building_status,
            owned_or_leased=params.owned_or_leased,
            limit=params.limit,
            offset=params.offset,
        )
    except IolpClientError as e:
        # Preserve attempted-source provenance when the error carries it.
        sa = [e.attempted_url] if isinstance(e, IolpFetchError) and e.attempted_url else []
        fat = e.fetch_attempted_at if isinstance(e, IolpFetchError) else None
        return IolpSearchOutput(
            total_found=0,
            returned=0,
            results=[],
            source_url=IOLP_DATASET_URL,
            sources_attempted=sa,
            sources_retrieved=[],
            fetch_attempted_at=fat,
            retrieved_at_utc=None,
            message=f"Could not retrieve IOLP data: {e}",
        ).model_dump()

    trunc_note = (
        f"Partial results: the processing limit was reached "
        f"({raw['records_scanned']:,} of up to {raw['row_limit']:,} "
        f"rows per dataset scanned). Counts and rankings describe only "
        f"the records scanned."
        if raw["truncated"] else None
    )

    if not raw["records"]:
        return IolpSearchOutput(
            total_found=0,
            returned=0,
            results=[],
            source_url=IOLP_DATASET_URL,
            sources_attempted=raw["sources_attempted"],
            sources_retrieved=raw["sources_retrieved"],
            fetch_attempted_at=raw["fetch_attempted_at"],
            retrieved_at_utc=raw["retrieved_at_utc"],
            truncated=raw["truncated"],
            row_limit=raw["row_limit"],
            records_scanned=raw["records_scanned"],
            message=trunc_note or "No matching federal properties found.",
        ).model_dump()

    results = []
    for rec in raw["records"]:
        ds = rec.get("_dataset", "unknown")
        sqft = rec.get("Building Rentable Square Feet")
        results.append(
            IolpPropertyRecord(
                location_code=rec.get("Location Code"),
                real_property_asset_name=rec.get("Real Property Asset Name"),
                dataset=ds,
                street_address=rec.get("Street Address"),
                city=rec.get("City"),
                state=rec.get("State"),
                zip_code=str(rec["Zip Code"]) if rec.get("Zip Code") is not None else None,
                gsa_region=int(rec["GSA Region"]) if rec.get("GSA Region") is not None else None,
                real_property_asset_type=rec.get(
                    "Real Property Asset Type" if ds == "buildings" else "Real Property Asset type"
                ),
                building_rentable_sq_ft=float(sqft) if sqft is not None else None,
                owned_or_leased=rec.get("Owned or Leased"),
                building_status=rec.get("Building Status"),
                lease_number=rec.get("Lease Number"),
                lease_effective_date=str(rec["Lease Effective Date"]) if rec.get("Lease Effective Date") is not None else None,
                lease_expiration_date=str(rec["Lease Expiration Date"]) if rec.get("Lease Expiration Date") is not None else None,
                source_url=IOLP_DATASET_URL,
            )
        )

    return IolpSearchOutput(
        total_found=raw["total"],
        returned=raw["returned"],
        results=results,
        source_url=IOLP_DATASET_URL,
        sources_attempted=raw["sources_attempted"],
        sources_retrieved=raw["sources_retrieved"],
        sources_sha256=raw.get("sources_sha256", []),
        fetch_attempted_at=raw["fetch_attempted_at"],
        retrieved_at_utc=raw["retrieved_at_utc"],
        truncated=raw["truncated"],
        row_limit=raw["row_limit"],
        records_scanned=raw["records_scanned"],
        message=trunc_note,
    ).model_dump()


@mcp.tool()
def get_property_details(
    location_code: str,
    dataset: str | None = None,
    lease_number: str | None = None,
) -> dict:
    """Retrieve full details for a single federal property by its IOLP Location Code.

    Searches the leases and/or buildings IOLP datasets. Returns the complete
    record including address, square footage, coordinates, congressional
    district, and lease or ownership information.

    When the same Location Code appears in both datasets, supply dataset=
    to select the intended record ('leases' or 'buildings').

    When a Location Code maps to multiple lease rows (e.g. CA6395), supply
    lease_number= (e.g. 'LCA00229') to pinpoint the specific lease record.

    Every response includes source_file (the exact XLSX URL queried),
    fetch_attempted_at, and retrieved_at_utc so results can be traced to
    a specific data snapshot. retrieved_at_utc is null when retrieval
    did not complete.

    Args:
        location_code: The IOLP Location Code, e.g. 'CT3414' or 'MA6048'.
        dataset: 'leases', 'buildings', or omit to search both.
        lease_number: Optional lease number disambiguator, e.g. 'LCA00229'.
    """
    try:
        params = IolpPropertyDetailInput(
            location_code=location_code,
            dataset=dataset,
            lease_number=lease_number,
        )
    except Exception as e:
        # Validation failed before any HTTP request.
        return IolpPropertyDetailOutput(
            dataset="unknown",
            source_url=IOLP_DATASET_URL,
            source_file=None,
            fetch_attempted_at=None,
            retrieved_at_utc=None,
            message=f"Invalid location code: {e}",
        ).model_dump()

    try:
        rec = get_property(
            params.location_code,
            dataset=params.dataset,
            lease_number=params.lease_number,
        )
    except IolpAmbiguousRecordError as e:
        # Fetch occurred but result is ambiguous — preserve all provenance fields.
        return IolpPropertyDetailOutput(
            dataset="unknown",
            source_url=IOLP_DATASET_URL,
            source_file=e.source_file,
            source_sha256=e.source_sha256,
            fetch_attempted_at=e.fetch_attempted_at,
            retrieved_at_utc=None,
            message=str(e),
        ).model_dump()
    except IolpRecordNotFoundError as e:
        # Fetch occurred but no record found — preserve all provenance fields.
        return IolpPropertyDetailOutput(
            dataset="unknown",
            source_url=IOLP_DATASET_URL,
            source_file=e.source_file,
            source_sha256=e.source_sha256,
            fetch_attempted_at=e.fetch_attempted_at,
            retrieved_at_utc=None,
            message=str(e),
        ).model_dump()
    except IolpClientError as e:
        # Preserve attempted-source provenance when the error carries it.
        sa = [e.attempted_url] if isinstance(e, IolpFetchError) and e.attempted_url else []
        fat = e.fetch_attempted_at if isinstance(e, IolpFetchError) else None
        return IolpPropertyDetailOutput(
            dataset="unknown",
            source_url=IOLP_DATASET_URL,
            source_file=None,
            fetch_attempted_at=fat,
            retrieved_at_utc=None,
            message=f"Could not retrieve property: {e}",
        ).model_dump()

    ds = rec.get("_dataset", "unknown")
    sqft = rec.get("Building Rentable Square Feet")
    avail = rec.get("Available Square Feet")
    lat = rec.get("Latitude")
    lon = rec.get("Longitude")
    region = rec.get("GSA Region")
    const = rec.get("Construction Date")

    return IolpPropertyDetailOutput(
        location_code=rec.get("Location Code"),
        real_property_asset_name=rec.get("Real Property Asset Name"),
        installation_name=rec.get("Installation Name"),
        dataset=ds,
        street_address=rec.get("Street Address"),
        city=rec.get("City"),
        state=rec.get("State"),
        zip_code=str(rec["Zip Code"]) if rec.get("Zip Code") is not None else None,
        latitude=float(lat) if lat is not None else None,
        longitude=float(lon) if lon is not None else None,
        gsa_region=int(region) if region is not None else None,
        real_property_asset_type=rec.get(
            "Real Property Asset Type" if ds == "buildings" else "Real Property Asset type"
        ),
        building_rentable_sq_ft=float(sqft) if sqft is not None else None,
        available_sq_ft=float(avail) if avail is not None else None,
        congressional_district=str(rec["Congressional District"]) if rec.get("Congressional District") is not None else None,
        owned_or_leased=rec.get("Owned or Leased"),
        building_status=rec.get("Building Status"),
        construction_date=int(const) if const is not None else None,
        federal_leased_code=rec.get("Federal Leased Code"),
        lease_number=rec.get("Lease Number"),
        lease_effective_date=str(rec["Lease Effective Date"]) if rec.get("Lease Effective Date") is not None else None,
        lease_expiration_date=str(rec["Lease Expiration Date"]) if rec.get("Lease Expiration Date") is not None else None,
        source_url=IOLP_DATASET_URL,
        source_file=rec.get("_source_file"),
        source_sha256=rec.get("_source_sha256"),
        fetch_attempted_at=rec.get("_fetch_attempted_at"),
        retrieved_at_utc=rec.get("_retrieved_at_utc"),
    ).model_dump()


if __name__ == "__main__":
    mcp.run()
