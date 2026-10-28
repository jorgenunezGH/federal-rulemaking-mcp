"""Tests for rate limit and upstream error handling.

Covers evaluation scenario S12. The Regulations.gov API returns HTTP 429
when the hourly quota is exhausted. These tests mock the HTTP layer rather
than exhausting the real quota, so they run offline and deterministically.
"""

from unittest.mock import Mock, patch

import pytest

from src.clients.regulations_client import (
    DocumentNotFoundError,
    RegulationsClientError,
    get_docket,
    get_document,
    search_documents,
)


def _response(status_code: int, text: str = "", payload: dict | None = None) -> Mock:
    """Build a mock requests.Response with the given status."""
    mock = Mock()
    mock.status_code = status_code
    mock.text = text
    mock.json.return_value = payload or {}
    return mock


# --- Rate limit (429) ---


@patch("src.clients.regulations_client.API_KEY", "TEST_KEY")
@patch("src.clients.regulations_client.requests.get")
def test_search_raises_on_rate_limit(mock_get):
    """A 429 on search raises RegulationsClientError with retry guidance."""
    mock_get.return_value = _response(429, "Too Many Requests")

    with pytest.raises(RegulationsClientError) as exc:
        search_documents("federal buildings")

    message = str(exc.value).lower()
    assert "rate limit" in message
    assert "retry" in message


@patch("src.clients.regulations_client.API_KEY", "TEST_KEY")
@patch("src.clients.regulations_client.requests.get")
def test_get_document_raises_on_rate_limit(mock_get):
    """A 429 on document retrieval raises with retry guidance."""
    mock_get.return_value = _response(429, "Too Many Requests")

    with pytest.raises(RegulationsClientError) as exc:
        get_document("FDA-2021-N-0270-0001")

    assert "rate limit" in str(exc.value).lower()


@patch("src.clients.regulations_client.API_KEY", "TEST_KEY")
@patch("src.clients.regulations_client.requests.get")
def test_get_docket_raises_on_rate_limit(mock_get):
    """A 429 on docket retrieval raises with retry guidance."""
    mock_get.return_value = _response(429, "Too Many Requests")

    with pytest.raises(RegulationsClientError) as exc:
        get_docket("FDA-2021-N-0270")

    assert "rate limit" in str(exc.value).lower()


@patch("src.clients.regulations_client.API_KEY", "TEST_KEY")
@patch("src.clients.regulations_client.requests.get")
def test_rate_limit_error_does_not_leak_response_body(mock_get):
    """The 429 message is a fixed string, not the upstream response text.

    Upstream error bodies may contain request context. The rate limit
    message is deliberately generic so nothing from the response is echoed
    back to the caller.
    """
    mock_get.return_value = _response(
        429, "Quota exceeded for api_key=SECRETVALUE123"
    )

    with pytest.raises(RegulationsClientError) as exc:
        search_documents("test")

    assert "SECRETVALUE123" not in str(exc.value)


# --- Not found (404) ---


@patch("src.clients.regulations_client.API_KEY", "TEST_KEY")
@patch("src.clients.regulations_client.requests.get")
def test_get_document_raises_not_found_on_404(mock_get):
    """A 404 raises DocumentNotFoundError, distinct from a general error."""
    mock_get.return_value = _response(404, "Not Found")

    with pytest.raises(DocumentNotFoundError) as exc:
        get_document("FAKE-9999-X-0000-0001")

    assert "FAKE-9999-X-0000-0001" in str(exc.value)


@patch("src.clients.regulations_client.API_KEY", "TEST_KEY")
@patch("src.clients.regulations_client.requests.get")
def test_not_found_is_subclass_of_client_error(mock_get):
    """DocumentNotFoundError can be caught as RegulationsClientError.

    Callers that do not need to distinguish a missing record from other
    failures can handle both with one except clause.
    """
    mock_get.return_value = _response(404, "Not Found")

    with pytest.raises(RegulationsClientError):
        get_document("FAKE-9999-X-0000-0001")


# --- Other upstream errors ---


@patch("src.clients.regulations_client.API_KEY", "TEST_KEY")
@patch("src.clients.regulations_client.requests.get")
def test_server_error_truncates_response_body(mock_get):
    """A 500 response body is truncated to limit leaked context."""
    long_body = "x" * 500
    mock_get.return_value = _response(500, long_body)

    with pytest.raises(RegulationsClientError) as exc:
        search_documents("test")

    message = str(exc.value)
    assert "500" in message
    # Body is truncated to 200 characters, so the full 500 must not appear.
    assert long_body not in message


@patch("src.clients.regulations_client.API_KEY", "TEST_KEY")
@patch("src.clients.regulations_client.requests.get")
def test_unauthorized_raises_client_error(mock_get):
    """A 403 raises a client error rather than returning empty results."""
    mock_get.return_value = _response(403, "Forbidden")

    with pytest.raises(RegulationsClientError) as exc:
        search_documents("test")

    assert "403" in str(exc.value)
    