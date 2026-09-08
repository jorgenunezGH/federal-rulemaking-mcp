"""Live check of the get_comment_requirements logic path.

Exercises the client, status resolution, and models directly, bypassing
the MCP transport layer. Run: python verify_comment_tool.py
"""

from datetime import datetime, timezone

from pydantic import ValidationError

from src.clients.regulations_client import DocumentNotFoundError, get_document
from src.comment_status import resolve_comment_status
from src.models import (
    CommentRequirementsInput,
    CommentRequirementsOutput,
    CommentStatus,
)

REG_GOV_DOC_BASE = "https://www.regulations.gov/document/"
REG_GOV_COMMENT_BASE = "https://www.regulations.gov/commenton/"


def check(document_id: str) -> None:
    """Resolve and print comment status for one document."""
    print(f"\n--- {document_id} ---")

    try:
        params = CommentRequirementsInput(document_id=document_id)
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
    resolved_id = data.get("id", "")
    now = datetime.now(timezone.utc)

    status, summary, remaining, elapsed = resolve_comment_status(attrs, now)
    accepting = status in (
        CommentStatus.OPEN,
        CommentStatus.CLOSED_LATE_ACCEPTED,
    )

    out = CommentRequirementsOutput(
        document_id=resolved_id,
        title=attrs.get("title", ""),
        status=status,
        summary=summary,
        accepting_comments=accepting,
        comment_start_date=attrs.get("commentStartDate"),
        comment_end_date=attrs.get("commentEndDate"),
        days_remaining=remaining,
        days_since_closed=elapsed,
        allows_late_comments=attrs.get("allowLateComments"),
        withdrawn=attrs.get("withdrawn"),
        source_url=f"{REG_GOV_DOC_BASE}{resolved_id}",
        comment_url=(
            f"{REG_GOV_COMMENT_BASE}{resolved_id}" if accepting else None
        ),
    )

    print(f"  Title:       {out.title[:58]}")
    print(f"  Status:      {out.status.value}")
    print(f"  Accepting:   {out.accepting_comments}")
    print(f"  Summary:     {out.summary}")
    print(f"  Window:      {out.comment_start_date} to {out.comment_end_date}")
    print(f"  Days remain: {out.days_remaining}")
    print(f"  Days since:  {out.days_since_closed}")
    print(f"  Late OK:     {out.allows_late_comments}")
    print(f"  Source:      {out.source_url}")
    print(f"  Comment URL: {out.comment_url}")

    # Show the raw flags so the resolution logic can be audited by hand.
    print(
        f"  [raw flags]  openForComment={attrs.get('openForComment')} "
        f"withinCommentPeriod={attrs.get('withinCommentPeriod')} "
        f"allowLateComments={attrs.get('allowLateComments')} "
        f"withdrawn={attrs.get('withdrawn')}"
    )


if __name__ == "__main__":
    print("Live verification: get_comment_requirements")

    # Known case from field discovery: openForComment=True but
    # withinCommentPeriod=False with late comments allowed. This is the
    # contradiction the tool exists to resolve.
    check("FDA-2021-N-0270-0001")

    # Supporting material with no comment window.
    check("EERE-2022-BT-TP-0028-0021")

    # Active DOE rulemaking on federal building energy standards.
    check("EERE-2010-BT-STD-0031-0128")

    # Live open comment period, DOE energy conservation program.
    # Window closes 2026-09-09 — expect this to change status after that.
    check("EERE-2022-BT-OT-0004-0001")

    # Longer-lived open window, retained so the OPEN branch stays
    # verifiable closer to submission.
    check("FAA-2026-9967-0001")
    
    check("FAKE-9999-X-0000-0001")
    check("not a valid id !!!")

    print("\nVerification complete.")
