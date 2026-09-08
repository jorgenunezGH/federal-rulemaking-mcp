"""Presentation demo: the analyst workflow, end to end.

Walks the read tools in the order an analyst would use them, with
narration between steps. Designed to be run on camera or projected.

Run: python demo.py
"""

import time
from datetime import datetime, timezone

from src.clients.regulations_client import get_docket, get_document, search_documents
from src.comment_status import resolve_comment_status

# Pacing between sections so a viewer can read each step.
PAUSE = 2.0

# Primary demo document: an FAA airspace rule with a comment window that
# stays open past the submission deadline. Chosen for durability, not
# subject relevance.
DEMO_DOC_ID = "FAA-2026-9967-0001"

# Contrast case: the API reports this as open for comment despite the
# window having closed in 2021.
CONTRADICTION_DOC_ID = "FDA-2021-N-0270-0001"

SEARCH_TERM = "federal buildings energy efficiency"

# Width at which titles are truncated for display.
TITLE_WIDTH = 68


def _present(value: object) -> str:
    """Render absent values explicitly rather than printing bare None."""
    return str(value) if value not in (None, "") else "not provided"


def _title(value: str | None) -> str:
    """Truncate a title with an ellipsis so cutoffs read as intentional."""
    text = value or ""
    if len(text) > TITLE_WIDTH:
        return text[:TITLE_WIDTH] + "..."
    return text or "not provided"


def header(text: str) -> None:
    """Print a section header and pause."""
    print()
    print("=" * 72)
    print(f"  {text}")
    print("=" * 72)
    time.sleep(PAUSE)


def narrate(text: str) -> None:
    """Print narration and pause briefly."""
    print(f"\n  > {text}")
    time.sleep(1.0)


def step_1_search() -> str:
    """Analyst searches for relevant rulemaking documents."""
    header("STEP 1  An analyst asks about relevant proposed rules")
    narrate(f'Searching Regulations.gov for: "{SEARCH_TERM}"')

    raw = search_documents(SEARCH_TERM, page_size=5)
    docs = raw.get("data", [])
    total = raw.get("meta", {}).get("totalElements", 0)

    print(f"\n  {total:,} documents match. Returning the first {len(docs)}:\n")
    for doc in docs:
        attrs = doc.get("attributes", {})
        print(f"    {doc.get('id')}")
        print(f"      {_title(attrs.get('title'))}")
        print(f"      https://www.regulations.gov/document/{doc.get('id')}")
        print()

    narrate("Every result carries a source URL. Provenance is not optional.")
    narrate(
        "Nothing here tells the analyst which of these are still open for "
        "comment. That is the question the fourth tool answers."
    )
    narrate(
        "For the rest of this walkthrough I will follow a document with a "
        "currently open window, so the deadline logic has something to show."
    )
    time.sleep(PAUSE)
    return DEMO_DOC_ID


def step_2_document(document_id: str) -> str | None:
    """Analyst retrieves full metadata for one document."""
    header("STEP 2  Retrieve the authoritative record")
    narrate(f"Requesting full metadata for {document_id}")

    raw = get_document(document_id)
    attrs = raw.get("data", {}).get("attributes", {})

    print()
    print(f"    Title:          {_title(attrs.get('title'))}")
    print(f"    Agency:         {_present(attrs.get('agencyId'))}")
    print(f"    Type:           {_present(attrs.get('documentType'))}")
    print(f"    Parent docket:  {_present(attrs.get('docketId'))}")
    print(f"    FR citation:    {_present(attrs.get('frVolNum'))}")
    print(f"    Posted:         {_present(attrs.get('postedDate'))}")

    narrate("The parent docket ID becomes the input to the next tool.")
    time.sleep(PAUSE)
    return attrs.get("docketId")


def step_3_docket(docket_id: str) -> None:
    """Analyst retrieves the parent docket for regulatory context."""
    header("STEP 3  Understand the broader regulatory action")
    narrate(f"Requesting docket {docket_id}")

    raw = get_docket(docket_id)
    attrs = raw.get("data", {}).get("attributes", {})

    docket_type = attrs.get("docketType")
    is_rulemaking = (
        docket_type.strip().lower() == "rulemaking" if docket_type else None
    )

    print()
    print(f"    Title:          {_title(attrs.get('title'))}")
    print(f"    Agency:         {_present(attrs.get('agencyId'))}")
    print(f"    Program:        {_present(attrs.get('program'))}")
    print(f"    Docket type:    {_present(docket_type)}")
    print(f"    Is rulemaking:  {is_rulemaking}")
    print(f"    RIN:            {_present(attrs.get('rin'))}")

    narrate(
        "is_rulemaking is derived, not returned by the API. The agent does "
        "not need to know that 'Nonrulemaking' is the negative case."
    )
    time.sleep(PAUSE)


def step_4_comment_status(document_id: str, label: str) -> None:
    """Analyst asks whether comments can still be filed."""
    header(f"STEP 4  Can we still comment?  ({label})")
    narrate(f"Resolving comment status for {document_id}")

    raw = get_document(document_id)
    attrs = raw.get("data", {}).get("attributes", {})
    now = datetime.now(timezone.utc)

    status, summary, remaining, elapsed = resolve_comment_status(attrs, now)

    print("\n    What the API returns:")
    print(f"      openForComment       = {attrs.get('openForComment')}")
    print(f"      withinCommentPeriod  = {attrs.get('withinCommentPeriod')}")
    print(f"      allowLateComments    = {attrs.get('allowLateComments')}")
    print(f"      commentEndDate       = {_present(attrs.get('commentEndDate'))}")

    print("\n    What the tool resolves:")
    print(f"      status               = {status.value}")
    print(f"      days_remaining       = {remaining}")
    print(f"      days_since_closed    = {elapsed}")
    print(f"\n      {summary}")
    time.sleep(PAUSE)


def run() -> None:
    """Run the full walkthrough."""
    print()
    print("  FEDERAL RULEMAKING READ MCP SERVER")
    print("  Analyst workflow demonstration")
    print("  Live calls to the Regulations.gov API v4")
    time.sleep(PAUSE)

    doc_id = step_1_search()
    docket_id = step_2_document(doc_id)

    if docket_id:
        step_3_docket(docket_id)

    step_4_comment_status(doc_id, "open window")
    step_4_comment_status(CONTRADICTION_DOC_ID, "the contradiction case")

    header("The point")
    print()
    print("  The API reported the second document as openForComment = True.")
    print("  Its comment window closed in May 2021.")
    print()
    print("  A thin API wrapper passes both flags to the agent and hopes.")
    print("  This tool resolves the contradiction into one defensible answer.")
    print()
    time.sleep(PAUSE)


if __name__ == "__main__":
    run()
