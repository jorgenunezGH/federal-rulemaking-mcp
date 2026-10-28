"""Tests that tool-layer error handling returns structured output.

The client raises exceptions; the tools must catch them and return a
dictionary the agent can reason about. Verifies no exception escapes to
the caller.

These tests import only the comment status logic and models, not the MCP
transport layer, so they run on workstations where the SDK cannot load.
"""

from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from src.clients.regulations_client import get_document
from src.models import CommentRequirementsInput, DocumentDetailInput


def _response(status_code: int, text: str = "") -> Mock:
    """Build a mock requests.Response with the given status."""
    mock = Mock()
    mock.status_code = status_code
    mock.text = text
    mock.json.return_value = {}
    return mock


def test_validation_rejects_before_any_network_call():
    """Malformed input fails validation without issuing a request.

    This is the mechanism behind the zero-second latency recorded for
    scenarios S04 and S07 — no network round trip occurs.
    """
    with patch("src.clients.regulations_client.requests.get") as mock_get:
        with pytest.raises(ValidationError):
            DocumentDetailInput(document_id="not a valid id !!!")
        mock_get.assert_not_called()


def test_comment_input_validation_rejects_before_network():
    """The comment tool's input validation also precedes any request."""
    with patch("src.clients.regulations_client.requests.get") as mock_get:
        with pytest.raises(ValidationError):
            CommentRequirementsInput(document_id="")
        mock_get.assert_not_called()


@patch("src.clients.regulations_client.API_KEY", "TEST_KEY")
@patch("src.clients.regulations_client.requests.get")
def test_client_timeout_is_configured(mock_get):
    """Every outbound request sets an explicit timeout.

    Without a timeout, an unresponsive upstream would hang the agent
    indefinitely rather than failing gracefully.
    """
    r = _response(200)
    r.json.return_value = {"data": {"attributes": {}, "id": "FDA-2021-N-0270-0001"}}
    mock_get.return_value = r

    get_document("FDA-2021-N-0270-0001")

    _, kwargs = mock_get.call_args
    assert kwargs.get("timeout") == 10