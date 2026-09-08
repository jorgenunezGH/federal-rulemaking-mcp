"""Thin client for the Regulations.gov API v4."""

import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.regulations.gov/v4"
API_KEY: str | None = os.getenv("REGULATIONS_API_KEY")


class RegulationsClientError(Exception):
    """Raised when the Regulations.gov API returns an error."""

class DocumentNotFoundError(RegulationsClientError):
    """Raised when a requested document or docket does not exist.

    Separated from the general client error so the tool layer can return
    a specific "not found" message to the agent rather than an opaque
    API failure. A missing record is an expected outcome, not a fault.
    """

def _headers() -> dict[str, str]:
    """Build request headers with the API key."""
    if not API_KEY:
        raise RegulationsClientError(
            "REGULATIONS_API_KEY not found. Check your .env file."
        )
    return {"X-Api-Key": API_KEY}


def search_documents(
    search_term: str,
    agency_id: str | None = None,
    document_type: str | None = None,
    page_size: int = 10,
) -> dict[str, Any]:
    """Search rulemaking documents. Returns raw API JSON."""
    params: dict[str, str | int] = {
        "filter[searchTerm]": search_term,
        "page[size]": page_size,
    }
    if agency_id:
        params["filter[agencyId]"] = agency_id
    if document_type:
        params["filter[documentType]"] = document_type

    resp = requests.get(
        f"{BASE_URL}/documents",
        headers=_headers(),
        params=params,
        timeout=10,
    )
    if resp.status_code == 429:
        raise RegulationsClientError(
            "Rate limit reached. Please wait and retry."
        )
    if resp.status_code != 200:
        raise RegulationsClientError(
            f"API error {resp.status_code}: {resp.text[:200]}"
        )

    data: dict[str, Any] = resp.json()
    return data

def get_document(document_id: str) -> dict[str, Any]:
    """Retrieve a single rulemaking document by its identifier.

    Returns the raw API JSON. Translation into the agent-facing shape
    happens in the tool layer so this client stays a thin, testable
    boundary around the HTTP call.
    """
    resp = requests.get(
        f"{BASE_URL}/documents/{document_id}",
        headers=_headers(),
        timeout=10,
    )

    if resp.status_code == 404:
        raise DocumentNotFoundError(
            f"No document found with ID '{document_id}'."
        )
    if resp.status_code == 429:
        raise RegulationsClientError(
            "Rate limit reached. Please wait and retry."
        )
    if resp.status_code != 200:
        raise RegulationsClientError(
            f"API error {resp.status_code}: {resp.text[:200]}"
        )

    data: dict[str, Any] = resp.json()
    return data

def get_docket(docket_id: str) -> dict[str, Any]:
    """Retrieve a single docket by its Regulations.gov identifier.

    Reuses DocumentNotFoundError rather than defining a separate docket
    exception — from the agent's perspective, "the record you asked for
    does not exist" is the same actionable outcome either way.
    """
    resp = requests.get(
        f"{BASE_URL}/dockets/{docket_id}",
        headers=_headers(),
        timeout=10,
    )

    if resp.status_code == 404:
        raise DocumentNotFoundError(
            f"No docket found with ID '{docket_id}'."
        )
    if resp.status_code == 429:
        raise RegulationsClientError(
            "Rate limit reached. Please wait and retry."
        )
    if resp.status_code != 200:
        raise RegulationsClientError(
            f"API error {resp.status_code}: {resp.text[:200]}"
        )

    data: dict[str, Any] = resp.json()
    return data
