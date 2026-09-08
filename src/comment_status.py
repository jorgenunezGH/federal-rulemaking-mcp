"""Comment period status resolution for Regulations.gov documents.

Deliberately free of MCP dependencies so the analytical logic can be
tested and executed independently of the transport layer. This also keeps
the status rules reviewable in isolation, without reading server plumbing.
"""

from datetime import datetime

from src.models import CommentStatus


def parse_api_date(value: str | None) -> datetime | None:
    """Parse a Regulations.gov ISO 8601 timestamp into an aware datetime.

    Returns None on absent or unparseable input rather than raising, so a
    single malformed date cannot fail an entire status assessment.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def resolve_comment_status(
    attrs: dict,
    now: datetime,
) -> tuple[CommentStatus, str, int | None, int | None]:
    """Collapse contradictory API comment flags into one status.

    Returns (status, summary, days_remaining, days_since_closed).

    Precedence is deliberate. Withdrawal is checked first because a
    withdrawn document cannot meaningfully accept comments regardless of
    its date fields. The API's withinCommentPeriod flag is then trusted
    over local date arithmetic, since an agency may adjust a window
    without the published dates reflecting the change immediately.
    """
    start = parse_api_date(attrs.get("commentStartDate"))
    end = parse_api_date(attrs.get("commentEndDate"))
    within = attrs.get("withinCommentPeriod")
    allows_late = bool(attrs.get("allowLateComments"))

    if attrs.get("withdrawn"):
        return (
            CommentStatus.WITHDRAWN,
            "This document has been withdrawn and is not accepting comments.",
            None,
            None,
        )

    if start is None and end is None:
        return (
            CommentStatus.NOT_APPLICABLE,
            "This document does not have a public comment period.",
            None,
            None,
        )

    if start and now < start:
        days_until = (start - now).days
        summary = (
            f"The comment period opens on {start.date().isoformat()} "
            f"({days_until} days from now)."
        )
        return (CommentStatus.NOT_YET_OPEN, summary, None, None)

    if within is True:
        remaining = (end - now).days if end else None

        # Clamp to zero. A negative count can arise when the API reports a
        # window as open while its published end date has already passed —
        # for example if an agency extended the period without updating the
        # date field. Reporting "-10 days remaining" would be misleading.
        if remaining is not None and remaining < 0:
            remaining = 0

        when = f" and closes on {end.date().isoformat()}" if end else ""
        if remaining == 0:
            tail = " (closing today or already past the published end date)"
        elif remaining is not None:
            tail = f" ({remaining} days remaining)"
        else:
            tail = ""

        summary = f"The comment period is currently open{when}{tail}."
        return (CommentStatus.OPEN, summary, remaining, None)

    if end and now > end:
        elapsed = (now - end).days
        if allows_late:
            summary = (
                f"The formal comment period closed on "
                f"{end.date().isoformat()} ({elapsed} days ago). This "
                f"document still accepts late comments, but submissions "
                f"fall outside the official window."
            )
            return (
                CommentStatus.CLOSED_LATE_ACCEPTED,
                summary,
                None,
                elapsed,
            )
        summary = (
            f"The comment period closed on {end.date().isoformat()} "
            f"({elapsed} days ago) and late comments are not accepted."
        )
        return (CommentStatus.CLOSED, summary, None, elapsed)

    # Dates exist but the flags do not resolve cleanly. Report the
    # uncertainty rather than guessing a status.
    summary = (
        "Comment period status could not be determined from the "
        "available fields. Consult the source record directly."
    )
    return (CommentStatus.NOT_APPLICABLE, summary, None, None)
