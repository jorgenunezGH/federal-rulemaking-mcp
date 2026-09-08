"""Validation tests for get_docket_details input handling.

Covers valid docket IDs and malformed docket ID rejection.
No API calls are made here.
"""

import pytest
from pydantic import ValidationError

from src.models import DocketDetailInput


def test_valid_docket_id_passes():
    """A well-formed docket ID validates."""
    inp = DocketDetailInput(docket_id="FDA-2021-N-0270")
    assert inp.docket_id == "FDA-2021-N-0270"


def test_longer_docket_id_passes():
    """Agencies use varying segment counts; a five-segment docket validates."""
    inp = DocketDetailInput(docket_id="EERE-2010-BT-STD-0031")
    assert inp.docket_id == "EERE-2010-BT-STD-0031"


def test_two_segment_docket_id_passes():
    """Dockets need only two segments, unlike documents which need three."""
    inp = DocketDetailInput(docket_id="EPA-2024")
    assert inp.docket_id == "EPA-2024"


def test_empty_docket_id_rejected():
    """An empty ID is rejected."""
    with pytest.raises(ValidationError):
        DocketDetailInput(docket_id="")


def test_malformed_docket_id_rejected():
    """Free text with spaces and punctuation is rejected."""
    with pytest.raises(ValidationError):
        DocketDetailInput(docket_id="not a docket !!!")


def test_single_segment_docket_id_rejected():
    """An ID with no hyphen is rejected."""
    with pytest.raises(ValidationError):
        DocketDetailInput(docket_id="FDA")
