"""Tests for the search tool — 1 success + 3 failure cases."""
import pytest
from pydantic import ValidationError

from src.models import SearchInput


def test_valid_input_passes():
    """Success: a normal search input validates."""
    inp = SearchInput(search_term="federal buildings")
    assert inp.search_term == "federal buildings"
    assert inp.page_size == 10


def test_empty_search_term_rejected():
    """Failure S04: empty search term is rejected."""
    with pytest.raises(ValidationError):
        SearchInput(search_term="")


def test_page_size_too_small_rejected():
    """Failure: page_size below 5 is rejected."""
    with pytest.raises(ValidationError):
        SearchInput(search_term="test", page_size=3)


def test_page_size_too_large_rejected():
    """Failure: page_size above 250 is rejected."""
    with pytest.raises(ValidationError):
        SearchInput(search_term="test", page_size=999)
        