"""Validation tests for get_document_details input handling.

Covers evaluation scenarios S05 (valid retrieval) and S07 (malformed ID
rejected before any network call). No API calls are made here.
"""

import pytest
from pydantic import ValidationError

from src.models import DocumentDetailInput


def test_valid_document_id_passes():
    """A well-formed agency document ID validates."""
    inp = DocumentDetailInput(document_id="FDA-2021-N-0270-0001")
    assert inp.document_id == "FDA-2021-N-0270-0001"


def test_longer_agency_id_passes():
    """Agencies use varying segment counts; a six-segment ID validates."""
    inp = DocumentDetailInput(document_id="EERE-2010-BT-STD-0031-0128")
    assert inp.document_id == "EERE-2010-BT-STD-0031-0128"


def test_empty_document_id_rejected():
    """An empty ID is rejected."""
    with pytest.raises(ValidationError):
        DocumentDetailInput(document_id="")


def test_malformed_document_id_rejected():
    """Free text with spaces and punctuation is rejected (S07)."""
    with pytest.raises(ValidationError):
        DocumentDetailInput(document_id="not a valid id !!!")


def test_too_few_segments_rejected():
    """An ID with fewer than three segments is rejected."""
    with pytest.raises(ValidationError):
        DocumentDetailInput(document_id="FDA-2021")
