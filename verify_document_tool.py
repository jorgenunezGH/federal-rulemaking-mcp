"""Live check of the get_document_details logic path.

Exercises the client and models directly, bypassing the MCP transport
layer (which is blocked locally). Run: python verify_document_tool.py
"""

from pydantic import ValidationError

from src.clients.regulations_client import (
    DocumentNotFoundError,
    get_document,
)
from src.models import DocumentDetailInput, DocumentDetailOutput

REG_GOV_BASE = "https://www.regulations.gov/document/"


def fetch(document_id: str) -> None:
    print(f"\n--- {document_id} ---")
        # Catch ValidationError specifically. A bare Exception here would mask
    # genuine defects in the model as if they were input rejections.
    try:
        params = DocumentDetailInput(document_id=document_id)
    except ValidationError:
        print("  Rejected at validation: ValidationError")
        return

    try:
        raw = get_document(params.document_id)
    except DocumentNotFoundError as e:
        print(f"  Not found (handled gracefully): {e}")
        return

    data = raw.get("data", {})
    attrs = data.get("attributes", {})

    out = DocumentDetailOutput(
        document_id=data.get("id", ""),
        title=attrs.get("title", ""),
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
        source_url=f"{REG_GOV_BASE}{data.get('id', '')}",
    )

    print(f"  Title:        {out.title[:60]}")
    print(f"  Type:         {out.document_type} / {out.subtype}")
    print(f"  Agency:       {out.agency_id}")
    print(f"  Docket:       {out.docket_id}")
    print(f"  FR citation:  {out.fr_citation}")
    print(f"  Open/within:  {out.open_for_comment} / {out.within_comment_period}")
    print(f"  Source:       {out.source_url}")


if __name__ == "__main__":
    print("Live verification: get_document_details")
    fetch("FDA-2021-N-0270-0001")        # S05 — valid
    fetch("EERE-2022-BT-TP-0028-0021")  # S05 — valid, second agency
    fetch("FAKE-9999-X-0000-0001")       # S06 — not found
    fetch("not a valid id !!!")          # S07 — malformed
    print("\nVerification complete.")
