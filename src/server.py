"""Federal Rulemaking Read MCP Server (MCP Python SDK v2).

Transport layer only. Analytical logic lives in src/comment_status.py and
HTTP interaction lives in src/clients/regulations_client.py, so each
concern can be tested and reviewed in isolation.
"""

from datetime import datetime, timezone

from mcp.server import MCPServer
from pydantic import ValidationError

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
    SearchInput,
    SearchOutput,
)

mcp = MCPServer("federal-rulemaking-read")

# Dockets, documents, and comment forms each use a distinct Regulations.gov
# URL path, so provenance links must be built from the correct base for the
# record type being returned.
REG_GOV_DOC_BASE = "https://www.regulations.gov/document/"
REG_GOV_DOCKET_BASE = "https://www.regulations.gov/docket/"
REG_GOV_COMMENT_BASE = "https://www.regulations.gov/commenton/"


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


if __name__ == "__main__":
    mcp.run()
