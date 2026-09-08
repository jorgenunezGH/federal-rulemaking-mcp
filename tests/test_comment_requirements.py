"""Tests for comment period status resolution.

Covers input validation plus the status precedence logic, which is the
analytical core of get_comment_requirements. The status tests use
synthetic attribute dictionaries so they run offline and deterministically
regardless of how live comment windows shift over time.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.comment_status import parse_api_date, resolve_comment_status
from src.models import CommentRequirementsInput, CommentStatus

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    """Render a datetime the way the Regulations.gov API does."""
    return dt.isoformat().replace("+00:00", "Z")


# --- Input validation ---


def test_valid_document_id_passes():
    """A well-formed document ID validates."""
    inp = CommentRequirementsInput(document_id="EERE-2022-BT-TP-0028-0021")
    assert inp.document_id == "EERE-2022-BT-TP-0028-0021"


def test_empty_document_id_rejected():
    """An empty ID is rejected."""
    with pytest.raises(ValidationError):
        CommentRequirementsInput(document_id="")


def test_malformed_document_id_rejected():
    """Free text is rejected before any network call."""
    with pytest.raises(ValidationError):
        CommentRequirementsInput(document_id="not an id !!!")


# --- Date parsing ---


def test_parse_api_date_handles_z_suffix():
    """The API's Z-suffixed timestamps parse correctly."""
    parsed = parse_api_date("2021-05-27T03:59:59Z")
    assert parsed is not None
    assert parsed.year == 2021


def test_parse_api_date_returns_none_on_garbage():
    """Unparseable input returns None rather than raising."""
    assert parse_api_date("not a date") is None


def test_parse_api_date_returns_none_on_empty():
    """Absent input returns None."""
    assert parse_api_date(None) is None


# --- Status resolution ---


def test_withdrawn_takes_precedence():
    """A withdrawn document reports WITHDRAWN regardless of dates."""
    attrs = {
        "withdrawn": True,
        "withinCommentPeriod": True,
        "commentStartDate": _iso(NOW - timedelta(days=5)),
        "commentEndDate": _iso(NOW + timedelta(days=5)),
    }
    status, summary, _, _ = resolve_comment_status(attrs, NOW)
    assert status is CommentStatus.WITHDRAWN
    assert "withdrawn" in summary.lower()


def test_no_dates_reports_not_applicable():
    """A document with no comment dates has no comment period."""
    status, _, _, _ = resolve_comment_status({}, NOW)
    assert status is CommentStatus.NOT_APPLICABLE


def test_future_start_reports_not_yet_open():
    """A comment period starting later reports NOT_YET_OPEN."""
    attrs = {
        "commentStartDate": _iso(NOW + timedelta(days=10)),
        "commentEndDate": _iso(NOW + timedelta(days=40)),
    }
    status, summary, _, _ = resolve_comment_status(attrs, NOW)
    assert status is CommentStatus.NOT_YET_OPEN
    assert "10 days" in summary


def test_open_period_reports_days_remaining():
    """An open period reports OPEN with a remaining-day count."""
    attrs = {
        "withinCommentPeriod": True,
        "commentStartDate": _iso(NOW - timedelta(days=5)),
        "commentEndDate": _iso(NOW + timedelta(days=20)),
    }
    status, summary, remaining, elapsed = resolve_comment_status(attrs, NOW)
    assert status is CommentStatus.OPEN
    assert remaining == 20
    assert elapsed is None
    assert "20 days remaining" in summary


def test_closed_with_late_comments_accepted():
    """A closed window that allows late comments is distinguished (S11)."""
    attrs = {
        "withinCommentPeriod": False,
        "allowLateComments": True,
        "openForComment": True,
        "commentStartDate": _iso(NOW - timedelta(days=100)),
        "commentEndDate": _iso(NOW - timedelta(days=60)),
    }
    status, summary, remaining, elapsed = resolve_comment_status(attrs, NOW)
    assert status is CommentStatus.CLOSED_LATE_ACCEPTED
    assert remaining is None
    assert elapsed == 60
    assert "late comments" in summary.lower()


def test_closed_without_late_comments():
    """A closed window with no late allowance reports CLOSED."""
    attrs = {
        "withinCommentPeriod": False,
        "allowLateComments": False,
        "commentStartDate": _iso(NOW - timedelta(days=100)),
        "commentEndDate": _iso(NOW - timedelta(days=60)),
    }
    status, summary, _, elapsed = resolve_comment_status(attrs, NOW)
    assert status is CommentStatus.CLOSED
    assert elapsed == 60
    assert "not accepted" in summary.lower()


def test_contradictory_flags_reports_uncertainty():
    """Unresolvable flag combinations report uncertainty, not a guess."""
    attrs = {
        "withinCommentPeriod": False,
        "commentStartDate": _iso(NOW - timedelta(days=10)),
        # End date in the future but not within period — ambiguous.
        "commentEndDate": _iso(NOW + timedelta(days=10)),
    }
    status, summary, _, _ = resolve_comment_status(attrs, NOW)
    assert status is CommentStatus.NOT_APPLICABLE
    assert "could not be determined" in summary.lower()

def test_within_period_flag_overrides_stale_end_date():
    """The API's withinCommentPeriod flag takes precedence over date math.

    An agency may extend a comment window without the published end date
    reflecting the change immediately. Trusting the flag over local
    arithmetic prevents reporting a period closed when the agency still
    considers it open. This is the reasoning behind the precedence order
    in resolve_comment_status.
    """
    attrs = {
        "withinCommentPeriod": True,
        "commentStartDate": _iso(NOW - timedelta(days=100)),
        # Published end date has passed, but the API reports the window open.
        "commentEndDate": _iso(NOW - timedelta(days=10)),
    }
    status, summary, remaining, elapsed = resolve_comment_status(attrs, NOW)

    assert status is CommentStatus.OPEN
    # Clamped rather than reported as -10, which would be misleading.
    assert remaining == 0
    assert elapsed is None
    assert "already past the published end date" in summary


def test_open_period_ending_today_reports_zero_days():
    """A window closing today reports zero days with a distinct message.

    Distinguished from the negative case above only by intent: here the
    dates are internally consistent, so the same clamped output is correct
    without any inference about agency behavior.
    """
    attrs = {
        "withinCommentPeriod": True,
        "commentStartDate": _iso(NOW - timedelta(days=30)),
        "commentEndDate": _iso(NOW),
    }
    status, summary, remaining, _ = resolve_comment_status(attrs, NOW)

    assert status is CommentStatus.OPEN
    assert remaining == 0
    assert "closing today" in summary
